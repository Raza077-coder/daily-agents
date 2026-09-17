"""Follow-up rule engine for JOBFLOW.

A job hunt does not fail from lack of effort — it fails from things quietly
going stale.  This module turns the vault into a ranked, explained to-do list.

Design rules
------------
* **Every action carries its reason.**  An action with no stated reason is
  indistinguishable from noise, so :func:`Action.explain` always produces a
  sentence derived from the numbers that triggered it.
* **Priority is a sum of named terms**, not a hand-tuned magic number buried in
  a branch.  :func:`_score` returns the breakdown alongside the total, so a
  surprising ordering can be traced.
* **Urgency dominates optimism.**  An exploding offer must outrank a stale
  wishlist item, so overdue/expiring terms are weighted far above staleness.
* **No rules here mutate anything.**  Plans are read-only descriptions; the
  caller decides what to apply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Sequence

from .analytics import STALL_DAYS, DEFAULT_STALL_DAYS
from .models import (
    ACTIVE,
    PRE,
    STAGE_INDEX,
    STAGES,
    STATUS_LABELS,
    Application,
    format_money,
)
from .store import Vault

#: Rules, most urgent first.  The order breaks priority ties deterministically.
RULE_ORDER: Sequence[str] = (
    "offer_expiring",
    "deadline_today",
    "deadline_soon",
    "interview_today",
    "interview_soon",
    "interview_prep",
    "offer_waiting",
    "overdue_next_action",
    "no_response_overdue",
    "stalled",
    "close_the_loop",
    "second_followup",
    "rejection_review",
    "weekly_target",
    "wishlist_nudge",
    "log_wins",
)

RULE_LABELS: Dict[str, str] = {
    "offer_expiring": "Offer decision due",
    "deadline_today": "Application deadline TODAY",
    "deadline_soon": "Deadline approaching",
    "interview_today": "Interview today",
    "interview_soon": "Interview coming up",
    "interview_prep": "Prep for interview",
    "offer_waiting": "Awaiting decision on offer",
    "overdue_next_action": "Next action overdue",
    "no_response_overdue": "Follow up — no response",
    "stalled": "Going stale",
    "close_the_loop": "Send a thank-you",
    "second_followup": "Second follow-up due",
    "rejection_review": "Learn from a rejection",
    "weekly_target": "Weekly application target",
    "wishlist_nudge": "Wishlist sitting idle",
    "log_wins": "Log your progress",
}

SEVERITIES = ("critical", "high", "medium", "low")

#: Named weights, so a score can be explained rather than merely asserted.
WEIGHTS = {
    "critical": 1000,
    "high": 500,
    "medium": 200,
    "low": 60,
    "overdue_per_day": 12,
    "overdue_cap": 240,
    "stale_per_day": 4,
    "stale_cap": 160,
    "priority_per_point": 15,
    "advanced_stage_bonus": {stage: index * 12 for index, stage in enumerate(STAGES)},
    "deadline_imminence": 30,
}

DEFAULT_WEEKLY_TARGET = 5
DEFAULT_FOLLOWUP_DAYS = 7
DEFAULT_SECOND_FOLLOWUP_DAYS = 14
DEFAULT_WISHLIST_IDLE_DAYS = 10


@dataclass
class Action:
    """One concrete next step, with the reasoning that produced it."""

    rule: str
    severity: str
    app_id: Optional[str]
    label: str
    summary: str
    action: str
    due_on: Optional[str] = None
    score: int = 0
    terms: Dict[str, int] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return RULE_LABELS.get(self.rule, self.rule)

    def explain(self) -> str:
        """A sentence naming the numbers behind this action."""

        if self.rule == "interview_prep" and self.details.get("days_away") is not None:
            return (
                f"{self.label}: {self.details['kind']} interview in "
                f"{self.details['days_away']} day(s) — prepare the stories and questions."
            )
        if self.rule in ("no_response_overdue", "second_followup", "stalled"):
            return (
                f"{self.label}: {self.details.get('days_since_activity')} days since the last "
                f"activity in {self.details.get('status_label')} "
                f"(threshold {self.details.get('threshold')} days)."
            )
        if self.rule == "overdue_next_action":
            return (
                f"{self.label}: next action {self.details.get('next_action')!r} was due "
                f"{self.due_on} — {self.details.get('overdue_days')} day(s) ago."
            )
        if self.rule in ("deadline_soon", "deadline_today"):
            return f"{self.label}: application deadline is {self.due_on}."
        return self.summary

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "title": self.title,
            "severity": self.severity,
            "score": self.score,
            "terms": dict(self.terms),
            "app_id": self.app_id,
            "label": self.label,
            "summary": self.summary,
            "explain": self.explain(),
            "action": self.action,
            "due_on": self.due_on,
            "details": dict(self.details),
        }


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def _score(
    severity: str,
    app: Optional[Application],
    *,
    overdue_days: int = 0,
    stale_days: int = 0,
    imminent: bool = False,
) -> tuple:
    """Return ``(total, terms)`` for an action.  Every point is attributable."""

    terms: Dict[str, int] = {}
    terms["severity"] = WEIGHTS.get(severity, 0)
    if overdue_days > 0:
        terms["overdue"] = min(overdue_days * WEIGHTS["overdue_per_day"], WEIGHTS["overdue_cap"])
    if stale_days > 0:
        terms["staleness"] = min(stale_days * WEIGHTS["stale_per_day"], WEIGHTS["stale_cap"])
    if app is not None:
        terms["priority"] = app.priority * WEIGHTS["priority_per_point"]
        stage = app.furthest_stage()
        if stage:
            terms["stage"] = WEIGHTS["advanced_stage_bonus"].get(stage, 0)
    if imminent:
        terms["imminence"] = WEIGHTS["deadline_imminence"]
    terms = {k: v for k, v in terms.items() if v}
    return sum(terms.values()), terms


def _config(config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = {
        "weekly_target": DEFAULT_WEEKLY_TARGET,
        # None means "use the per-status STALL_DAYS table" (applied 14,
        # screen 10, ...).  A number here overrides every status at once.
        "followup_days": None,
        "second_followup_days": DEFAULT_SECOND_FOLLOWUP_DAYS,
        "wishlist_idle_days": DEFAULT_WISHLIST_IDLE_DAYS,
        "max_followups": 2,
    }
    for key, value in (config or {}).items():
        if key in merged and value is not None:
            try:
                merged[key] = int(value)
            except (TypeError, ValueError):
                continue
    return merged


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------


def _rule_dates(app: Application, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """Deadline and interview rules — the time-critical ones."""

    if app.deadline_on and app.status in PRE:
        deadline = date.fromisoformat(app.deadline_on)
        days_away = (deadline - today).days
        if days_away < 0:
            return
        if days_away == 0:
            score, terms = _score("critical", app, imminent=True)
            out.append(
                Action(
                    rule="deadline_today",
                    severity="critical",
                    app_id=app.id,
                    label=app.label,
                    summary=f"Submit today — the deadline is {app.deadline_on}.",
                    action=f"Submit the application for {app.label} today.",
                    due_on=app.deadline_on,
                    score=score,
                    terms=terms,
                    details={"days_away": 0, "url": app.url},
                )
            )
        elif days_away <= 3:
            severity = "high" if days_away <= 1 else "medium"
            score, terms = _score(severity, app, overdue_days=max(0, 3 - days_away))
            out.append(
                Action(
                    rule="deadline_soon",
                    severity=severity,
                    app_id=app.id,
                    label=app.label,
                    summary=f"Deadline in {days_away} day(s) ({app.deadline_on}).",
                    action=f"Finish and submit the application for {app.label}.",
                    due_on=app.deadline_on,
                    score=score,
                    terms=terms,
                    details={"days_away": days_away, "url": app.url},
                )
            )

    for interview in app.upcoming_interviews(today):
        days_away = (interview.date - today).days
        if days_away > 7:
            continue
        pretty = interview.kind.replace("_", " ")
        if days_away == 0:
            rule, severity, action = (
                "interview_today",
                "critical",
                f"Interview TODAY: {pretty} with {app.company}. Join early and have questions ready.",
            )
        elif days_away <= 1:
            rule, severity, action = (
                "interview_soon",
                "high",
                f"Final prep for the {pretty} interview with {app.company} tomorrow.",
            )
        else:
            rule, severity, action = (
                "interview_prep",
                "medium",
                f"Prepare for the {pretty} interview with {app.company} in {days_away} days.",
            )
        score, terms = _score(severity, app, imminent=days_away <= 1)
        out.append(
            Action(
                rule=rule,
                severity=severity,
                app_id=app.id,
                label=app.label,
                summary=f"{pretty.title()} interview on {interview.on} ({days_away} day(s)).",
                action=action,
                due_on=interview.on,
                score=score,
                terms=terms,
                details={
                    "days_away": days_away,
                    "kind": pretty,
                    "interview_note": interview.note,
                },
            )
        )


def _rule_offer(app: Application, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """An open offer is the highest-value thing in any job hunt."""

    if app.status != "offer":
        return
    entered = app.stage_entered_on()
    days = (today - entered).days if entered else 0
    if days >= 5:
        score, terms = _score("critical", app, overdue_days=days - 5 + 1)
        out.append(
            Action(
                rule="offer_expiring",
                severity="critical",
                app_id=app.id,
                label=app.label,
                summary=f"Offer outstanding for {days} days.",
                action=(
                    f"Decide on {app.company} — respond to the offer, ask about the deadline, "
                    "or negotiate. Silence reads as disinterest."
                ),
                score=score,
                terms=terms,
                details={"days_open": days, "salary": format_money(app.salary_max, app.currency)},
            )
        )
    else:
        score, terms = _score("high", app)
        out.append(
            Action(
                rule="offer_waiting",
                severity="high",
                app_id=app.id,
                label=app.label,
                summary=f"Offer received {days} day(s) ago.",
                action=f"Reply to {app.company}: acknowledge the offer and ask for the decision deadline.",
                score=score,
                terms=terms,
                details={"days_open": days},
            )
        )


def _rule_followups(app: Application, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """Silence rules: the follow-up cadence and stall detection."""

    if not app.is_active or not app.was_applied:
        return
    # An open offer is owned by the offer rules above; a "follow-up" nudge
    # there would duplicate the offer action with contradictory advice.
    if app.status == "offer":
        return
    # Configurable cadence: an explicit followup_days (config.yaml or
    # JOBFLOW_FOLLOWUP_DAYS) overrides every status at once; when it is unset
    # the per-status STALL_DAYS table applies.  Previously followup_days was
    # documented and accepted but never read, so changing it did nothing.
    threshold = int(cfg.get("followup_days") or STALL_DAYS.get(app.status, DEFAULT_STALL_DAYS))
    quiet = app.days_since_activity(today)
    if quiet < threshold:
        return

    count = app.followup_count()
    if count == 0:
        rule, severity = "no_response_overdue", "high" if quiet >= threshold * 2 else "medium"
        action = (
            f"Send a short follow-up to {app.company} — reference the role and restate your interest."
        )
    elif count < cfg["max_followups"]:
        rule, severity = "second_followup", "medium"
        action = f"Send one final follow-up to {app.company}, then stop and move on."
    else:
        rule, severity = "stalled", "low"
        action = (
            f"Stop chasing {app.company} — you have followed up {count} time(s). "
            "Mark it rejected or withdrawn and put the effort into new applications."
        )

    score, terms = _score(severity, app, stale_days=quiet - threshold + 1, overdue_days=quiet - threshold)
    out.append(
        Action(
            rule=rule,
            severity=severity,
            app_id=app.id,
            label=app.label,
            summary=f"{quiet} days quiet in {STATUS_LABELS[app.status]} ({count} follow-up(s) sent).",
            action=action,
            due_on=(today - timedelta(days=quiet - threshold)).isoformat(),
            score=score,
            terms=terms,
            details={
                "days_since_activity": quiet,
                "status_label": STATUS_LABELS[app.status],
                "threshold": threshold,
                "followups_sent": count,
                "contact": app.contact,
            },
        )
    )


def _rule_next_action(app: Application, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """Your own explicitly-set commitments, enforced."""

    if not app.next_action_on or not app.next_action:
        return
    due = date.fromisoformat(app.next_action_on)
    overdue = (today - due).days
    if overdue < 1:
        return
    severity = "critical" if overdue >= 7 else "high" if overdue >= 3 else "medium"
    score, terms = _score(severity, app, overdue_days=overdue)
    out.append(
        Action(
            rule="overdue_next_action",
            severity=severity,
            app_id=app.id,
            label=app.label,
            summary=f"'{app.next_action}' was due {app.next_action_on} ({overdue} day(s) overdue).",
            action=f"Do it now: {app.next_action} ({app.label}).",
            due_on=app.next_action_on,
            score=score,
            terms=terms,
            details={"overdue_days": overdue, "next_action": app.next_action},
        )
    )


def _rule_thankyou(app: Application, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """A completed interview with no follow-up is a wasted opportunity."""

    if not app.is_active:
        return
    done = [i for i in app.interviews if i.done]
    if not done:
        return
    latest = max(done, key=lambda i: i.date)
    days_since = (today - latest.date).days
    if not 1 <= days_since <= 3:
        return
    already = any(
        e.kind == "followup" and date.fromisoformat(e.on) >= latest.date for e in app.events
    )
    if already:
        return
    score, terms = _score("high", app, overdue_days=days_since)
    out.append(
        Action(
            rule="close_the_loop",
            severity="high",
            app_id=app.id,
            label=app.label,
            summary=f"{latest.kind.replace('_', ' ').title()} interview {days_since} day(s) ago — no thank-you logged.",
            action=(
                f"Send a thank-you note to {app.contact or 'your interviewer'} at {app.company} "
                "and restate one concrete reason you fit."
            ),
            due_on=latest.on,
            score=score,
            terms=terms,
            details={"days_since_interview": days_since, "kind": latest.kind, "contact": app.contact},
        )
    )


def _rule_learning(app: Application, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """A rejection after an interview is the most valuable event in the hunt."""

    if app.status != "rejected" or app.reached_index() < STAGE_INDEX["interview"]:
        return
    if not app.closed_on:
        return
    days_since = (today - date.fromisoformat(app.closed_on)).days
    if not 0 <= days_since <= 10:
        return
    if any(n.text.lower().startswith("post-mortem") for n in app.notes):
        return
    stage = app.furthest_stage() or "applied"
    score, terms = _score("medium", app, overdue_days=days_since)
    out.append(
        Action(
            rule="rejection_review",
            severity="medium",
            app_id=app.id,
            label=app.label,
            summary=f"Rejected at the {stage} rung {days_since} day(s) ago.",
            action=(
                f"Write a post-mortem note for {app.label}: what was asked, where it went thin, "
                "and the one thing to drill before the next loop."
            ),
            score=score,
            terms=terms,
            details={"reached_stage": stage, "days_since": days_since},
        )
    )


def _rule_wishlist(app: Application, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """Wishlist items are a queue — but a queue that rots."""

    if app.status not in PRE:
        return
    if cfg["wishlist_idle_days"] <= 0:
        return
    last = app.last_activity_on()
    idle = (today - last).days if last else 0
    if idle < cfg["wishlist_idle_days"]:
        return
    severity = "low" if idle < cfg["wishlist_idle_days"] * 3 else "medium"
    score, terms = _score(severity, app, stale_days=idle - cfg["wishlist_idle_days"] + 1)
    out.append(
        Action(
            rule="wishlist_nudge",
            severity=severity,
            app_id=app.id,
            label=app.label,
            summary=f"On the wishlist {idle} day(s) without progress.",
            action=f"Either apply to {app.role} at {app.company} or drop it off the list.",
            due_on=last.isoformat() if last else None,
            score=score,
            terms=terms,
            details={"idle_days": idle},
        )
    )


def _rule_weekly(vault: Vault, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """A pipeline-level target, so the plan is never empty."""

    target = cfg["weekly_target"]
    if target <= 0:
        return
    week_start = today - timedelta(days=today.weekday())
    this_week = [
        a
        for a in vault.applications
        if a.applied_on and date.fromisoformat(a.applied_on) >= week_start
    ]
    remaining = days_left_in_week(today)
    shortfall = target - len(this_week)
    if shortfall <= 0:
        return
    severity = "high" if days_left_in_week(today) <= 1 else "medium"
    score, terms = _score(severity, None, overdue_days=shortfall)
    terms["shortfall"] = shortfall * 5
    score += terms["shortfall"]
    out.append(
        Action(
            rule="weekly_target",
            severity=severity,
            app_id=None,
            label="Weekly target",
            summary=(
                f"{len(this_week)}/{target} applications sent this week with "
                f"{remaining} day(s) left."
            ),
            action=f"Send {shortfall} more application(s) before the week ends.",
            due_on=(week_start + timedelta(days=6)).isoformat(),
            score=score,
            terms=terms,
            details={"sent": len(this_week), "target": target, "shortfall": shortfall},
        )
    )


def days_left_in_week(today: date) -> int:
    """Days remaining in the current ISO week, counting today."""

    return 7 - today.weekday()


def _rule_momentum(vault: Vault, today: date, cfg: Dict[str, Any], out: List[Action]) -> None:
    """Celebrate and record wins — a hunt with no recorded progress feels dead."""

    received = [
        a
        for a in vault.applications
        if a.status in ("offer", "accepted")
        and (entered := a.stage_entered_on())
        and (today - entered).days <= 7
    ]
    if not received:
        return
    names = ", ".join(a.company for a in received[:3])
    score, terms = _score("low", None)
    out.append(
        Action(
            rule="log_wins",
            severity="low",
            app_id=None,
            label="Momentum",
            summary=f"{len(received)} offer/accepted in the last 7 days.",
            action=f"Record the outcome for {names} and note what worked while it is fresh.",
            score=score,
            terms=terms,
            details={"companies": [a.company for a in received]},
        )
    )


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def actions(
    vault: Vault, today: date, config: Optional[Dict[str, Any]] = None
) -> List[Action]:
    """Every action the vault justifies today, ranked by explained score."""

    cfg = _config(config)
    found: List[Action] = []

    for app in vault.applications:
        _rule_dates(app, today, cfg, found)
        _rule_offer(app, today, cfg, found)
        _rule_next_action(app, today, cfg, found)
        _rule_followups(app, today, cfg, found)
        _rule_thankyou(app, today, cfg, found)
        _rule_learning(app, today, cfg, found)
        _rule_wishlist(app, today, cfg, found)

    _rule_weekly(vault, today, cfg, found)
    _rule_momentum(vault, today, cfg, found)

    rule_rank = {name: i for i, name in enumerate(RULE_ORDER)}
    found.sort(key=lambda a: (-a.score, rule_rank.get(a.rule, 99), a.app_id or ""))
    return found


def plan(
    vault: Vault,
    today: date,
    limit: int = 10,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """The ranked to-do list plus a workload summary."""

    cfg = _config(config)
    all_actions = actions(vault, today, config)
    by_severity: Dict[str, int] = {sev: 0 for sev in SEVERITIES}
    for action in all_actions:
        by_severity[action.severity] = by_severity.get(action.severity, 0) + 1

    top = all_actions[: max(0, int(limit))]
    return {
        "today": today.isoformat(),
        "total": len(all_actions),
        "by_severity": by_severity,
        "config": cfg,
        "actions": [a.to_dict() for a in top],
        "headline": _headline(all_actions, cfg),
    }


def _headline(all_actions: List[Action], cfg: Dict[str, Any]) -> str:
    if not all_actions:
        return "Nothing is burning. Send another application or take the evening off."
    critical = [a for a in all_actions if a.severity == "critical"]
    if critical:
        first = critical[0]
        return f"{len(critical)} urgent item(s). Start with: {first.action}"
    high = [a for a in all_actions if a.severity == "high"]
    if high:
        return f"{len(high)} important item(s). Next up: {high[0].action}"
    return f"{len(all_actions)} thing(s) worth doing today. Start with: {all_actions[0].action}"


def explain_action(
    vault: Vault, today: date, rule: str, app_id: Optional[str] = None
) -> Dict[str, Any]:
    """Why did this rule fire?  Returns the matching action or an explanation."""

    if rule not in RULE_LABELS:
        return {
            "rule": rule,
            "found": False,
            "reason": f"unknown rule {rule!r}; known rules: {', '.join(RULE_ORDER)}",
        }
    for action in actions(vault, today):
        if action.rule == rule and (app_id is None or action.app_id == app_id):
            return {"rule": rule, "found": True, "action": action.to_dict()}
    return {
        "rule": rule,
        "found": False,
        "reason": (
            f"rule {rule!r} did not fire for the current vault"
            + (f" and application {app_id!r}" if app_id else "")
        ),
    }
