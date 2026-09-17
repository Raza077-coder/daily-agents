"""The facade every surface talks to: CLI, Python, REST and the browser demo.

Anything a caller wants to do to a vault goes through :class:`JobFlowEngine`.
Keeping one entry point means the CLI, the API and the tests cannot drift into
three subtly different behaviours — and it is what the parity harness pins the
JavaScript port against.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import analytics, followups, report
from .models import (
    ACTIVE,
    CLOSED,
    PRE,
    SOURCE_LABELS,
    SOURCES,
    STAGE_INDEX,
    STAGES,
    STATUS_LABELS,
    STATUSES,
    TERMINAL,
    WORK_MODES,
    Application,
    Event,
    Interview,
    JobFlowError,
    Note,
    NotFoundError,
    ValidationError,
    parse_date,
)
from .pipeline import Pipeline
from .store import Vault, default_vault_path

#: Sensible defaults, overridable per call or from the vault's config block.
DEFAULT_CONFIG: Dict[str, Any] = {
    "weekly_target": 5,
    # None = per-status thresholds from analytics.STALL_DAYS.  Set a number
    # (JOBFLOW_FOLLOWUP_DAYS) to override them all at once.
    "followup_days": None,
    "second_followup_days": 14,
    "wishlist_idle_days": 10,
    "currency": "USD",
}


class JobFlowEngine:
    """High-level operations over a :class:`Vault`."""

    def __init__(
        self,
        vault: Optional[Vault] = None,
        path: Optional[Any] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.path = Path(path) if path else (vault.path if vault and vault.path else None)
        self.vault = vault if vault is not None else Vault.load(self.path)
        self.pipeline = Pipeline(self.vault)
        self.config = dict(DEFAULT_CONFIG)
        if config:
            self.config.update({k: v for k, v in config.items() if v is not None})

    # -- plumbing ----------------------------------------------------------

    @classmethod
    def open(cls, path: Optional[Any] = None, **kwargs: Any) -> "JobFlowEngine":
        """Open (or create) a vault at ``path``, defaulting to ``$JOBFLOW_VAULT``."""

        target = Path(path) if path else default_vault_path()
        vault = Vault.load(target)
        engine = cls(vault=vault, path=target, **kwargs)
        return engine

    @classmethod
    def from_dict(cls, data: Dict[str, Any], config: Optional[Dict[str, Any]] = None):
        """Build an in-memory engine (used by the API and the parity harness)."""

        return cls(vault=Vault.from_dict(data), path=None, config=config)

    def save(self, path: Optional[Any] = None) -> Path:
        return self.vault.save(Path(path) if path else self.path)

    def resolve_today(self, today: Optional[Any]) -> date:
        """Resolve the reference date for any calculation.

        An explicit ``today`` always wins.  When it is omitted we fall back to
        the system clock — that is the one place in the engine allowed to read
        it, and keeping the fallback here (rather than in each caller) is what
        makes ``GET /api/summary`` work without repeating ``?today=`` on every
        request.  Calculations themselves stay pure: they always receive a
        concrete date.
        """

        if today is None or today == "":
            return date.today()
        parsed = parse_date(today, "today")
        if parsed is None:
            return date.today()
        return parsed

    # -- vocabulary (so clients need not hardcode it) ----------------------

    @staticmethod
    def vocabulary() -> Dict[str, Any]:
        return {
            "stages": list(STAGES),
            "statuses": list(STATUSES),
            "active": list(ACTIVE),
            "closed": list(CLOSED),
            "pre": list(PRE),
            "terminal": list(TERMINAL),
            "work_modes": list(WORK_MODES),
            "sources": list(SOURCES),
            "source_labels": dict(SOURCE_LABELS),
            "status_labels": dict(STATUS_LABELS),
            "stage_index": dict(STAGE_INDEX),
            "stall_days": dict(analytics.STALL_DAYS),
            "stage_maturity_days": dict(analytics.STAGE_MATURITY_DAYS),
            "rules": list(followups.RULE_ORDER),
            "rule_labels": dict(followups.RULE_LABELS),
            "severities": list(followups.SEVERITIES),
        }

    # -- writes ------------------------------------------------------------

    def add(self, company: str, role: str, today: Optional[Any] = None, **fields: Any) -> Application:
        when = self.resolve_today(today)
        return self.pipeline.add(company, role, today=when, **fields)

    def add_many(self, rows: Sequence[Dict[str, Any]], today: Optional[Any] = None) -> List[Application]:
        when = self.resolve_today(today)
        return self.pipeline.add_many(list(rows), today=when)

    def move(self, app_id: str, to_status: str, today: Optional[Any] = None, **kw: Any) -> Application:
        return self.pipeline.move(app_id, to_status, today=self.resolve_today(today), **kw)

    def log_apply(self, app_id: str, today: Optional[Any] = None, **kw: Any) -> Application:
        return self.pipeline.log_apply(app_id, today=self.resolve_today(today), **kw)

    def log_stage(self, app_id: str, stage: str, today: Optional[Any] = None, **kw: Any) -> Application:
        return self.pipeline.log_stage(app_id, stage, today=self.resolve_today(today), **kw)

    def reopen(self, app_id: str, today: Optional[Any] = None, **kw: Any) -> Application:
        return self.pipeline.reopen(app_id, today=self.resolve_today(today), **kw)

    def schedule_interview(self, app_id: str, on: Any, **kw: Any):
        return self.pipeline.schedule_interview(app_id, on, **kw)

    def complete_interview(self, app_id: str, today: Optional[Any] = None, **kw: Any) -> Application:
        return self.pipeline.complete_interview(app_id, today=self.resolve_today(today), **kw)

    def note(self, app_id: str, text: str, today: Optional[Any] = None, **kw: Any) -> Application:
        return self.pipeline.note(app_id, text, today=self.resolve_today(today), **kw)

    def followup(self, app_id: str, today: Optional[Any] = None, **kw: Any) -> Application:
        return self.pipeline.followup(app_id, today=self.resolve_today(today), **kw)

    def set_next_action(self, app_id: str, action: str, on: Optional[Any] = None) -> Application:
        return self.pipeline.set_next_action(app_id, action, on=on)

    def edit(self, app_id: str, today: Optional[Any] = None, **changes: Any) -> Application:
        return self.pipeline.edit(app_id, today=self.resolve_today(today), **changes)

    def drop(self, app_id: str) -> Application:
        return self.pipeline.drop(app_id)

    # -- reads -------------------------------------------------------------

    def get(self, app_id: str) -> Application:
        return self.vault.get(app_id)

    def list(
        self,
        statuses: Optional[Iterable[str]] = None,
        active_only: bool = False,
        tag: Optional[str] = None,
        company: Optional[str] = None,
    ) -> List[Application]:
        return self.vault.filter(
            statuses=statuses, active_only=active_only, tag=tag, company=company
        )

    def summary(self, today: Optional[Any] = None) -> Dict[str, Any]:
        return analytics.summary(self.vault, self.resolve_today(today))

    def funnel(self, today: Optional[Any] = None) -> Dict[str, Any]:
        return analytics.funnel(self.vault, self.resolve_today(today))

    def plan(self, today: Optional[Any] = None, limit: int = 10) -> Dict[str, Any]:
        return followups.plan(
            self.vault, self.resolve_today(today), limit=limit, config=self.config
        )

    def explain(self, rule: str, app_id: Optional[str] = None, today: Optional[Any] = None) -> Dict[str, Any]:
        return followups.explain_action(
            self.vault, self.resolve_today(today), rule, app_id=app_id
        )

    def upcoming(self, today: Optional[Any] = None, days: int = 14) -> List[Dict[str, Any]]:
        when = self.resolve_today(today)
        horizon = when.toordinal() + max(0, int(days))
        rows: List[Dict[str, Any]] = []
        for app in self.vault.applications:
            for interview in app.interviews:
                if interview.done:
                    continue
                stamp = interview.date.toordinal()
                if when.toordinal() <= stamp <= horizon:
                    rows.append(
                        {
                            "app_id": app.id,
                            "label": app.label,
                            "company": app.company,
                            "role": app.role,
                            "on": interview.on,
                            "kind": interview.kind,
                            "days_away": stamp - when.toordinal(),
                            "note": interview.note,
                            "contact": app.contact,
                        }
                    )
        rows.sort(key=lambda r: (r["on"], r["app_id"]))
        return rows

    # -- rendering ---------------------------------------------------------

    def render_status(self, today: Optional[Any] = None) -> str:
        return report.status_report(self.vault, self.resolve_today(today), self.config)

    def render_board(self, today: Optional[Any] = None, statuses: Optional[Sequence[str]] = None) -> str:
        return report.board(self.vault, self.resolve_today(today), statuses)

    def render_plan(self, today: Optional[Any] = None) -> str:
        return report.followup_report(self.vault, self.resolve_today(today), self.config)

    def render_report(self, today: Optional[Any] = None, weeks: int = 12) -> str:
        return report.full_report(self.vault, self.resolve_today(today), self.config, weeks=weeks)

    def render_markdown(self, today: Optional[Any] = None) -> str:
        return report.markdown_report(self.vault, self.resolve_today(today), self.config)

    def export(self, today: Optional[Any] = None) -> Dict[str, Any]:
        return report.export_bundle(self.vault, self.resolve_today(today), self.config)

    # -- demo data ---------------------------------------------------------

    def seed_demo(self, today: Optional[Any] = None, force: bool = False) -> Dict[str, Any]:
        """Load a realistic 90-day job hunt so the UI has something to show.

        The dataset is hand-built, not random: it contains one referral that
        converted to an offer, a rejection *after* an onsite (which is the case
        that breaks naive funnels), a stalled application, and a wishlist item
        with a near deadline.
        """

        when = self.resolve_today(today)
        if self.vault.applications and not force:
            raise ValidationError(
                "vault is not empty — pass force=true to replace the contents with demo data"
            )
        if force:
            self.vault.applications = []

        def back(days: int) -> str:
            from datetime import timedelta

            return (when - timedelta(days=days)).isoformat()

        def fwd(days: int) -> str:
            from datetime import timedelta

            return (when + timedelta(days=days)).isoformat()

        def build(company: str, role: str, **kw: Any) -> Application:
            # A backdated application must also be backdated at creation, or
            # the "applied before created" invariant rejects it.
            if kw.get("applied_on") and not kw.get("created_on"):
                kw["created_on"] = kw["applied_on"]
            app = self.pipeline.add(company, role, today=when, **kw)
            return app

        # 1. A referral that became an offer — the success story.
        northwind = build(
            "Northwind Labs",
            "Senior Backend Engineer",
            status="applied",
            applied_on=back(58),
            location="Berlin, DE",
            work_mode="hybrid",
            source="referral",
            currency="EUR",
            salary_min="95000",
            salary_max="115000",
            priority=5,
            contact="marta.klien@northwind.example",
            url="https://northwind.example/jobs/senior-backend",
            tags=["python", "dream"],
        )
        northwind.events.append(
            Event(
                on=back(50), kind="moved", from_status="applied", to_status="screen",
                note="recruiter call booked",
            )
        )
        northwind.status = "offer"
        self.pipeline.schedule_interview(northwind.id, back(40), kind="screen", note="30 min with Marta")
        self.pipeline.complete_interview(northwind.id, on=back(40), today=when, note="went well")
        self.pipeline.schedule_interview(northwind.id, back(28), kind="technical", note="live coding, Python")
        self.pipeline.complete_interview(northwind.id, on=back(28), today=when, note="system design round")
        self.pipeline.schedule_interview(northwind.id, back(12), kind="final", note="with the CTO")
        self.pipeline.complete_interview(northwind.id, on=back(12), today=when, note="strong signal")
        northwind.status = "offer"

        # 2. Rejected AFTER an onsite — the cohort-bias case.
        cobalt = build(
            "Cobalt Systems",
            "Platform Engineer",
            status="applied",
            applied_on=back(44),
            location="Remote (EU)",
            work_mode="remote",
            source="linkedin",
            currency="EUR",
            salary_min="88000",
            salary_max="98000",
            priority=4,
            contact="hiring@cobalt.example",
            tags=["kubernetes"],
        )
        self.pipeline.log_stage(cobalt.id, "onsite", on=back(18), today=when, note="half-day panel")
        cobalt.status = "rejected"
        cobalt.closed_on = back(9)
        cobalt.events.append(
            Event(
                on=back(9), kind="closed", from_status="onsite", to_status="rejected",
                note="went with an internal candidate",
            )
        )

        # 3. Waiting quietly after a screen — triggers the follow-up rule.
        hyperion = build(
            "Hyperion Retail",
            "Data Engineer",
            status="applied",
            applied_on=back(26),
            location="Karachi, PK",
            work_mode="onsite",
            source="job_board",
            currency="PKR",
            salary_min="450000",
            salary_max="650000",
            priority=3,
            tags=["spark"],
        )
        self.pipeline.log_stage(hyperion.id, "screen", on=back(20), today=when, note="HR screening")
        hyperion.status = "screen"
        hyperion.events.append(
            Event(
                on=back(20), kind="moved", from_status="applied", to_status="screen",
                note="HR screening passed",
            )
        )
        hyperion.next_action = "Chase the hiring manager for the technical round"
        hyperion.next_action_on = back(2)

        # 4. Fresh application — too young to judge.
        brightpath = build(
            "Brightpath Health",
            "Python Developer",
            status="applied",
            applied_on=back(5),
            location="Remote",
            work_mode="remote",
            source="company_site",
            currency="USD",
            salary_min="70000",
            salary_max="90000",
            priority=4,
            tags=["healthtech"],
        )
        self.pipeline.schedule_interview(brightpath.id, fwd(3), kind="screen", note="recruiter intro")

        # 5. Stalled with two follow-ups already sent.
        quarry = build(
            "Quarry Analytics",
            "ML Engineer",
            status="applied",
            applied_on=back(71),
            location="London, UK",
            work_mode="hybrid",
            source="recruiter",
            currency="GBP",
            salary_min="80000",
            salary_max="95000",
            priority=2,
            tags=["ml"],
        )
        self.pipeline.followup(quarry.id, today=when, note="pinged recruiter")
        quarry.events.append(
            Event(on=back(40), kind="followup", note="second ping, still nothing")
        )
        quarry.events.sort(key=lambda e: (e.on, e.kind))

        # 6. Wishlist with a deadline three days out.
        atlas = build(
            "Atlas Fintech",
            "Staff Engineer",
            status="wishlist",
            location="Dubai, AE",
            work_mode="onsite",
            source="networking",
            currency="AED",
            salary_min="280000",
            salary_max="360000",
            priority=4,
            deadline_on=fwd(3),
            url="https://atlas.example/careers/staff-engineer",
            tags=["fintech"],
        )
        atlas.notes.append(Note(on=back(4), text="Referred by Sara — mention her."))

        # 7. Withdrawn — honest bookkeeping.
        veridian = build(
            "Veridian Media",
            "Backend Developer",
            status="applied",
            applied_on=back(33),
            location="Remote",
            work_mode="remote",
            source="job_board",
            currency="USD",
            priority=1,
            tags=["media"],
        )
        veridian.status = "withdrawn"
        veridian.closed_on = back(15)
        veridian.events.append(
            Event(
                on=back(15), kind="closed", from_status="applied", to_status="withdrawn",
                note="role was re-scoped to a contract",
            )
        )

        self.vault.owner = self.vault.owner or "Demo user"
        for app in self.vault.applications:
            app.validate()
        self.vault.sort()
        return {
            "seeded": len(self.vault.applications),
            "owner": self.vault.owner,
            "today": when.isoformat(),
        }
