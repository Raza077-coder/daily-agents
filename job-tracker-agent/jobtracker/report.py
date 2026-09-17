"""Report rendering for JOBFLOW — text digests, boards and JSON for pipelines.

Reporting is separated from analytics so the maths can be tested without
snapshotting prose.  Everything here is pure rendering: pass the same bundle in
and you get the same string out, byte for byte.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Dict, Iterable, List, Optional, Sequence

from . import analytics, followups
from .models import (
    ACTIVE,
    CLOSED,
    PRE,
    SOURCE_LABELS,
    STAGE_INDEX,
    STAGES,
    STATUS_LABELS,
    Application,
    format_money,
    format_range,
)
from .store import Vault

SEVERITY_MARK: Dict[str, str] = {
    "critical": "!!!",
    "high": "!! ",
    "medium": "!  ",
    "low": "   ",
}

STATUS_MARK: Dict[str, str] = {
    "wishlist": "·",
    "applied": "→",
    "screen": "◔",
    "interview": "◑",
    "onsite": "◕",
    "offer": "★",
    "accepted": "✔",
    "rejected": "✘",
    "withdrawn": "⊘",
}


def _rule(width: int = 68, char: str = "─") -> str:
    return char * width


def _bar(value: float, maximum: float, width: int = 22) -> str:
    """A text bar.  Deterministic rounding — no float accumulation."""

    if maximum <= 0:
        return "░" * width
    filled = int(round(value / maximum * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _pct(value: float) -> str:
    return f"{value:5.1f}%"


# --------------------------------------------------------------------------
# Board
# --------------------------------------------------------------------------


def board(vault: Vault, today: date, statuses: Optional[Sequence[str]] = None) -> str:
    """A one-line-per-application board that fits in a terminal."""

    wanted = list(statuses) if statuses else list(STATUS_LABELS)
    grouped: Dict[str, List[Application]] = {status: [] for status in wanted}
    for app in vault.applications:
        if app.status in grouped:
            grouped[app.status].append(app)

    lines: List[str] = []
    lines.append(f"JOBFLOW board — {today.isoformat()}")
    lines.append(_rule(68, "═"))

    for status in wanted:
        apps = grouped.get(status) or []
        if not apps:
            continue
        header = f"{STATUS_MARK.get(status, ' ')} {STATUS_LABELS[status]} ({len(apps)})"
        lines.append("")
        lines.append(header)
        for app in sorted(apps, key=lambda a: (-a.priority, a.id)):
            quiet = app.days_since_activity(today)
            age = app.age_days(today)
            threshold = analytics.STALL_DAYS.get(app.status, analytics.DEFAULT_STALL_DAYS)
            flag = " ⚠" if app.is_active and quiet >= threshold else ""
            meta_bits = [f"{age}d"]
            if app.is_active:
                meta_bits.append(f"quiet {quiet}d")
            salary = format_range(app.salary_min, app.salary_max, app.currency, compact=True)
            if salary != "—":
                meta_bits.append(salary)
            lines.append(
                f"   {STATUS_MARK.get(status, ' ')} {app.label[:44]:<44} "
                f"[{' · '.join(meta_bits)}]{flag}"
            )
            lines.append(f"      id: {app.id}")
            if app.next_action:
                due = f" (due {app.next_action_on})" if app.next_action_on else ""
                lines.append(f"      next: {app.next_action}{due}")

    if not vault.applications:
        lines.append("")
        lines.append("  (empty vault — add an application with `jobflow add`)")
    lines.append("")
    lines.append(_rule(68, "═"))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Status / daily digest
# --------------------------------------------------------------------------


def status_report(vault: Vault, today: date, config: Optional[Dict[str, Any]] = None) -> str:
    """The daily 'what is going on' digest — funnel, alerts, next steps."""

    bundle = analytics.summary(vault, today)
    todo = followups.plan(vault, today, limit=8, config=config)
    lines: List[str] = []

    fun = bundle["funnel"]
    lines.append(f"JOBFLOW — {today.strftime('%A %d %B %Y')}")
    lines.append(_rule(68, "═"))
    lines.append(
        f"  {bundle['totals']['submitted']} submitted · {bundle['totals']['open']} open · "
        f"{bundle['totals']['wishlist']} wishlist · {bundle['totals']['closed']} closed"
    )
    if fun["response_denominator"] == 0:
        lines.append(
            f"  Response rate n/a "
            f"(no application has matured past {analytics.STAGE_MATURITY_DAYS['applied']} days yet)"
        )
    else:
        lines.append(
            f"  Response rate {_pct(fun['response_rate'])} "
            f"({fun['responded']}/{fun['response_denominator']} mature applications)"
        )
    lines.append("")

    # -- funnel ---------------------------------------------------------
    lines.append("FUNNEL")
    lines.append(_rule(68))
    top = max((row["reached"] for row in fun["rows"]), default=0) or 1
    for row in fun["rows"]:
        lines.append(
            f"  {row['label']:<17} {_bar(row['reached'], top)} {row['reached']:>3}"
            f"  {_pct(row['step_rate'])} step"
        )
    lines.append("")
    by_stage = {row["stage"]: row for row in fun["rows"]}
    lines.append(
        f"  Reached an interview: {fun['interviewed']} · "
        f"Onsite/final: {by_stage['onsite']['reached']} · Offers: {fun['offered']}"
    )
    lines.append("")

    # -- alerts ---------------------------------------------------------
    if todo["actions"]:
        lines.append("TODAY'S PLAN")
        lines.append(_rule(68))
        for index, action in enumerate(todo["actions"], start=1):
            mark = SEVERITY_MARK.get(action["severity"], "   ")
            lines.append(f"  {index:>2}. {mark} {action['action']}")
            lines.append(f"      {action['explain']}")
        lines.append("")
    else:
        lines.append("TODAY'S PLAN")
        lines.append(_rule(68))
        lines.append(f"  {todo['headline']}")
        lines.append("")

    # -- stalls ---------------------------------------------------------
    if bundle["stalled"]:
        lines.append("GOING QUIET")
        lines.append(_rule(68))
        for row in bundle["stalled"][:6]:
            lines.append(
                f"  {row['label'][:40]:<40} {row['days_since_activity']:>3}d quiet "
                f"in {row['status_label']}"
            )
        lines.append("")

    # -- weekly ---------------------------------------------------------
    lines.append("WEEKLY THROUGHPUT (last 6 weeks)")
    lines.append(_rule(68))
    recent = bundle["weekly"][-6:]
    peak = max((row["applied"] for row in recent), default=1) or 1
    for row in recent:
        lines.append(
            f"  {row['iso_week']}  {_bar(row['applied'], peak, 16)} "
            f"{row['applied']} applied · {row['interviews']} interview(s)"
        )
    lines.append(
        f"  Streak: {bundle['streak']['current_weeks']} week(s) current · "
        f"{bundle['streak']['longest_weeks']} week(s) best"
    )
    lines.append("")

    # -- sources --------------------------------------------------------
    if bundle["sources"]:
        lines.append("WHAT IS WORKING (by source)")
        lines.append(_rule(68))
        for row in bundle["sources"][:5]:
            lines.append(
                f"  {row['label']:<15} {row['applied']:>3} applied · "
                f"{row['interviewed']:>2} interview(s) · {_pct(row['interview_rate'])}"
            )
        lines.append("")

    lines.append(_rule(68, "═"))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Follow-up plan
# --------------------------------------------------------------------------


def followup_report(vault: Vault, today: date, config: Optional[Dict[str, Any]] = None) -> str:
    bundle = followups.plan(vault, today, limit=50, config=config)
    lines: List[str] = []
    lines.append(f"JOBFLOW plan — {today.isoformat()}")
    lines.append(_rule(68, "═"))
    lines.append(f"  {bundle['headline']}")
    lines.append(
        "  " + " · ".join(f"{sev}: {n}" for sev, n in bundle["by_severity"].items() if n)
    )
    lines.append("")

    if not bundle["actions"]:
        lines.append("  Nothing scheduled. Nice work.")
        lines.append(_rule(68, "═"))
        return "\n".join(lines)

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for action in bundle["actions"]:
        grouped.setdefault(action["title"], []).append(action)

    for title, items in grouped.items():
        lines.append(title.upper())
        lines.append(_rule(68))
        for action in items:
            mark = SEVERITY_MARK.get(action["severity"], "   ")
            who = action["label"]
            lines.append(f"  {mark} {who}")
            lines.append(f"      {action['action']}")
            lines.append(f"      why: {action['explain']}  (score {action['score']})")
        lines.append("")
    lines.append(_rule(68, "═"))
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Full report / Markdown / JSON
# --------------------------------------------------------------------------


def full_report(
    vault: Vault,
    today: date,
    config: Optional[Dict[str, Any]] = None,
    weeks: int = 12,
) -> str:
    bundle = analytics.summary(vault, today)
    lines: List[str] = []
    lines.append("=" * 68)
    lines.append(f"JOBFLOW REPORT — {today.isoformat()}")
    lines.append("=" * 68)
    lines.append("")
    lines.append(status_report(vault, today, config))
    lines.append("")

    # -- time in stage ---------------------------------------------------
    lines.append("TIME IN STAGE (completed stretches only)")
    lines.append(_rule(68))
    tis = bundle["time_in_stage"]
    for stage in STAGES:
        row = tis.get(stage) or {}
        if not row.get("samples"):
            continue
        lines.append(
            f"  {row['label']:<17} median {row['median_days']:>3}d "
            f"(min {row['min_days']}, max {row['max_days']}, n={row['samples']})"
        )
    lines.append("")

    # -- aging -----------------------------------------------------------
    lines.append("OPEN APPLICATIONS BY AGE")
    lines.append(_rule(68))
    if bundle["aging"]:
        for row in bundle["aging"][:15]:
            flag = "⚠" if row["stalled"] else " "
            lines.append(
                f" {flag} {row['label'][:36]:<36} {row['age_days']:>4}d old · "
                f"{row['days_in_stage']:>3}d in {row['status_label']}"
            )
    else:
        lines.append("  (no open applications)")
    lines.append("")

    # -- salary ----------------------------------------------------------
    salary = bundle["salary"]
    if salary["entries"]:
        lines.append("COMPENSATION (never summed across currencies)")
        lines.append(_rule(68))
        for currency, row in salary["currencies"].items():
            lines.append(
                f"  {currency}: {row['count']} role(s) · median {row['median_display']} "
                f"· range {row['min_display']}–{row['max_display']}"
            )
        lines.append("")

    lines.append(_rule(68, "="))
    return "\n".join(lines)


def markdown_report(
    vault: Vault, today: date, config: Optional[Dict[str, Any]] = None
) -> str:
    """A report that pastes cleanly into Notion, GitHub or an email."""

    bundle = analytics.summary(vault, today)
    todo = followups.plan(vault, today, limit=10, config=config)
    fun = bundle["funnel"]
    out: List[str] = []

    out.append(f"# JOBFLOW report — {today.isoformat()}")
    out.append("")
    out.append(
        f"**{bundle['totals']['submitted']}** submitted · **{bundle['totals']['open']}** open · "
        f"**{bundle['totals']['wishlist']}** wishlist · **{bundle['totals']['closed']}** closed"
    )
    out.append("")
    if fun["response_denominator"] == 0:
        out.append(
            f"Response rate: not measurable yet — no application has matured "
            f"past {analytics.STAGE_MATURITY_DAYS['applied']} days, so there is nothing "
            f"to divide by. ({fun['submitted']} submitted.)"
        )
    else:
        out.append(
            f"Response rate **{fun['response_rate']}%** "
            f"({fun['responded']}/{fun['response_denominator']} mature applications)"
        )
    out.append("")

    out.append("## Funnel")
    out.append("")
    out.append("| Stage | Reached | Step rate | Cumulative | Maturity window |")
    out.append("|---|---:|---:|---:|---:|")
    for row in fun["rows"]:
        out.append(
            f"| {row['label']} | {row['reached']} | {row['step_rate']}% | "
            f"{row['rate']}% | {row['maturity_days']}d |"
        )
    out.append("")

    out.append("## Plan")
    out.append("")
    if todo["actions"]:
        for action in todo["actions"]:
            out.append(f"- **[{action['severity']}]** {action['action']}")
            out.append(f"  - _{action['explain']}_ (score {action['score']})")
    else:
        out.append(f"- {todo['headline']}")
    out.append("")

    if bundle["sources"]:
        out.append("## Sources")
        out.append("")
        out.append("| Source | Applied | Interviewed | Interview rate |")
        out.append("|---|---:|---:|---:|")
        for row in bundle["sources"]:
            out.append(
                f"| {row['label']} | {row['applied']} | {row['interviewed']} | "
                f"{row['interview_rate']}% |"
            )
        out.append("")

    if bundle["stalled"]:
        out.append("## Going quiet")
        out.append("")
        for row in bundle["stalled"]:
            out.append(
                f"- **{row['label']}** — {row['days_since_activity']}d quiet "
                f"in {row['status_label']}"
            )
        out.append("")

    return "\n".join(out)


def to_json(payload: Any) -> str:
    """Stable JSON for pipelines: sorted keys, no NaN, trailing newline."""

    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"


def export_bundle(
    vault: Vault, today: date, config: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """The machine-readable twin of :func:`full_report`."""

    return {
        "generated_on": today.isoformat(),
        "vault": vault.to_dict(),
        "summary": analytics.summary(vault, today),
        "plan": followups.plan(vault, today, limit=25, config=config),
        "funnel_rows": analytics.funnel_rows(vault, today),
    }
