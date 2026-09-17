"""Analytics for JOBFLOW — funnel, response rates, aging and time-in-stage.

The central correctness problem this module solves is **cohort bias**.

A naive conversion rate is ``reached(stage) / applied``.  That number is wrong,
and it is wrong in the direction that makes a job hunt look better than it is:
when a rejection lands, most trackers leave the application sitting at
``applied`` forever, so the denominator keeps growing while the numerator never
does — and the conversion rate silently decays for reasons that have nothing
to do with your interview skill.

JOBFLOW derives the furthest rung from the *event log* instead, so a rejection
after an onsite still counts as having reached the onsite rung.  Two further
guards are needed for the rate to mean anything:

1. **Only submitted applications count.**  Wishlist items are excluded from
   every rate — they were never in the funnel.
2. **Only "mature" applications count in the denominator.**  An application
   submitted two days ago has not *failed* to get a response; it is simply too
   early to know.  Counting it as a non-response is the mistake that makes a
   healthy search look dead.  Each stage therefore carries a *maturity window*
   (see :data:`STAGE_MATURITY_DAYS`) and only applications older than that
   window enter the denominator.

Both rules are applied everywhere and are reported explicitly, so a rate can
always be traced back to the rows behind it.
"""

from __future__ import annotations

from datetime import date, timedelta
from statistics import median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

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

#: How long to wait before treating "no progress past stage N" as information.
#:
#: Derived from typical hiring timelines: a recruiter screen normally lands
#: within two weeks, a first interview within three, and a final loop within
#: six.  An application younger than the window is *unknown*, not a failure.
STAGE_MATURITY_DAYS: Dict[str, int] = {
    "applied": 21,
    "screen": 30,
    "interview": 45,
    "onsite": 60,
    "offer": 75,
    "accepted": 90,
}

#: Days of silence after which an open application is considered stalled.
STALL_DAYS: Dict[str, int] = {
    "applied": 14,
    "screen": 10,
    "interview": 12,
    "onsite": 10,
    "offer": 7,
}

DEFAULT_STALL_DAYS = 14


# --------------------------------------------------------------------------
# Funnel
# --------------------------------------------------------------------------


def funnel_rows(vault: Vault, today: date) -> List[Dict[str, Any]]:
    """Per-rung counts, with the denominator each rate is computed against."""

    submitted = [a for a in vault.applications if a.was_applied]
    total = len(submitted)
    rows: List[Dict[str, Any]] = []

    for index, stage in enumerate(STAGES):
        reached = [a for a in submitted if a.reached_index() >= index]
        window = STAGE_MATURITY_DAYS[stage]
        mature = [a for a in submitted if a.age_days(today) >= window] if index > 0 else submitted
        denominator = len(mature) if index > 0 else total
        rate = (len(reached) / denominator * 100.0) if denominator else 0.0

        previous_reached = (
            [a for a in submitted if a.reached_index() >= index - 1] if index > 0 else reached
        )
        if index == 0:
            step_rate = 100.0 if total else 0.0
        else:
            step_rate = (
                len(reached) / len(previous_reached) * 100.0 if previous_reached else 0.0
            )

        rows.append(
            {
                "stage": stage,
                "label": STATUS_LABELS[stage],
                "reached": len(reached),
                "denominator": denominator,
                "rate": round(rate, 1),
                "step_rate": round(step_rate, 1),
                "maturity_days": window,
            }
        )
    return rows


def funnel(vault: Vault, today: date) -> Dict[str, Any]:
    """The funnel plus the headline numbers a job seeker actually quotes."""

    submitted = [a for a in vault.applications if a.was_applied]
    rows = funnel_rows(vault, today)
    by_stage = {row["stage"]: row for row in rows}

    responded = [a for a in submitted if a.has_response()]
    screened = [a for a in submitted if a.reached_index() >= STAGE_INDEX["screen"]]
    interviewed = [a for a in submitted if a.reached_index() >= STAGE_INDEX["interview"]]
    offered = [a for a in submitted if a.reached_index() >= STAGE_INDEX["offer"]]

    response_denominator = [
        a for a in submitted if a.age_days(today) >= STAGE_MATURITY_DAYS["applied"]
    ]
    response_rate = (
        len(responded) / len(response_denominator) * 100.0 if response_denominator else 0.0
    )

    open_apps = [a for a in vault.applications if a.is_active]
    return {
        "today": today.isoformat(),
        "submitted": len(submitted),
        "open": len(open_apps),
        "wishlist": sum(1 for a in vault.applications if a.status in PRE),
        "closed": sum(1 for a in vault.applications if a.is_closed),
        "responded": len(responded),
        "response_denominator": len(response_denominator),
        "response_rate": round(response_rate, 1),
        "screened": len(screened),
        "interviewed": len(interviewed),
        "offered": len(offered),
        "rows": rows,
        "offer_rate": by_stage["offer"]["rate"],
        "interview_rate": by_stage["interview"]["rate"],
        "onsite_rate": by_stage["onsite"]["rate"],
    }


# --------------------------------------------------------------------------
# Time in stage / aging
# --------------------------------------------------------------------------


def aging_rows(vault: Vault, today: date) -> List[Dict[str, Any]]:
    """Every open application with its age and how long it has been quiet."""

    out: List[Dict[str, Any]] = []
    for app in vault.applications:
        if not app.is_active:
            continue
        threshold = STALL_DAYS.get(app.status, DEFAULT_STALL_DAYS)
        quiet = app.days_since_activity(today)
        last = app.last_activity_on()
        out.append(
            {
                "id": app.id,
                "label": app.label,
                "status": app.status,
                "status_label": STATUS_LABELS[app.status],
                "age_days": app.age_days(today),
                "days_in_stage": app.days_in_stage(today),
                "days_since_activity": quiet,
                "stall_threshold": threshold,
                "stalled": quiet >= threshold,
                "last_activity_on": last.isoformat() if last else None,
                "priority": app.priority,
                "next_action": app.next_action,
                "next_action_on": app.next_action_on,
            }
        )
    out.sort(key=lambda r: (-r["days_since_activity"], -r["priority"], r["id"]))
    return out


def stalled(vault: Vault, today: date) -> List[Dict[str, Any]]:
    return [row for row in aging_rows(vault, today) if row["stalled"]]


def time_in_stage(vault: Vault, today: date) -> Dict[str, Any]:
    """Median days spent in each stage, over applications that left it.

    Only *completed* stretches are counted: an application still sitting in
    ``applied`` has not finished its time there, and folding it in would drag
    the median toward whoever happens to be waiting longest right now.
    """

    samples: Dict[str, List[int]] = {stage: [] for stage in STAGES}

    for app in vault.applications:
        if not app.was_applied:
            continue
        entered = app.applied_on and date.fromisoformat(app.applied_on)
        if entered is None:
            continue
        # Every event that leaves a stage closes that stage's stretch.
        transitions: List[Tuple[date, Optional[str], Optional[str]]] = []
        for event in app.events:
            stage = event.to_status
            if stage in STAGE_INDEX:
                transitions.append((event.date, event.from_status, stage))
        transitions.sort(key=lambda t: t[0])

        cursor_stage = "applied"
        cursor_start = entered
        for stamp, from_status, to_status in transitions:
            if to_status == cursor_stage:
                continue
            if cursor_stage in samples and stamp >= cursor_start:
                samples[cursor_stage].append((stamp - cursor_start).days)
            cursor_stage = to_status
            cursor_start = stamp

        # A closed application ends the final stretch.
        if app.closed_on and app.status in CLOSED:
            end = date.fromisoformat(app.closed_on)
            if cursor_stage in samples and end >= cursor_start:
                samples[cursor_stage].append((end - cursor_start).days)

    out: Dict[str, Any] = {}
    for stage, values in samples.items():
        clean = [v for v in values if v >= 0]
        out[stage] = {
            "stage": stage,
            "label": STATUS_LABELS[stage],
            "samples": len(clean),
            "median_days": int(median(clean)) if clean else None,
            "min_days": min(clean) if clean else None,
            "max_days": max(clean) if clean else None,
            "mean_days": round(sum(clean) / len(clean), 1) if clean else None,
        }
    return out


# --------------------------------------------------------------------------
# Sources & segmentation
# --------------------------------------------------------------------------


def source_breakdown(vault: Vault, today: date) -> List[Dict[str, Any]]:
    """Which channels actually produce interviews, not just applications."""

    submitted = [a for a in vault.applications if a.was_applied]
    buckets: Dict[str, List[Application]] = {}
    for app in submitted:
        buckets.setdefault(app.source, []).append(app)

    rows: List[Dict[str, Any]] = []
    for source, apps in buckets.items():
        responded = [a for a in apps if a.has_response()]
        interviewed = [a for a in apps if a.reached_index() >= STAGE_INDEX["interview"]]
        offered = [a for a in apps if a.reached_index() >= STAGE_INDEX["offer"]]
        mature = [a for a in apps if a.age_days(today) >= STAGE_MATURITY_DAYS["applied"]]
        rows.append(
            {
                "source": source,
                "label": SOURCE_LABELS.get(source, source),
                "applied": len(apps),
                "responded": len(responded),
                "interviewed": len(interviewed),
                "offered": len(offered),
                "response_rate": round(len(responded) / len(mature) * 100.0, 1) if mature else 0.0,
                "interview_rate": round(len(interviewed) / len(apps) * 100.0, 1) if apps else 0.0,
            }
        )
    rows.sort(key=lambda r: (-r["interview_rate"], -r["applied"], r["source"]))
    return rows


def salary_summary(vault: Vault) -> Dict[str, Any]:
    """Salary ranges, reported per currency (never summed across currencies)."""

    by_currency: Dict[str, Dict[str, Any]] = {}
    for app in vault.applications:
        if app.salary_min is None and app.salary_max is None:
            continue
        bucket = by_currency.setdefault(
            app.currency, {"currency": app.currency, "count": 0, "mins": [], "maxs": []}
        )
        bucket["count"] += 1
        if app.salary_min is not None:
            bucket["mins"].append(app.salary_min)
        if app.salary_max is not None:
            bucket["maxs"].append(app.salary_max)

    out: Dict[str, Any] = {"currencies": {}, "entries": 0}
    for currency, bucket in by_currency.items():
        mins, maxs = bucket["mins"], bucket["maxs"]
        out["currencies"][currency] = {
            "currency": currency,
            "count": bucket["count"],
            "min": min(mins) if mins else None,
            "max": max(maxs) if maxs else None,
            "median_min": int(median(mins)) if mins else None,
            "median_max": int(median(maxs)) if maxs else None,
            "min_display": format_money(min(mins), currency, compact=True) if mins else "—",
            "max_display": format_money(max(maxs), currency, compact=True) if maxs else "—",
            "median_display": format_range(
                int(median(mins)) if mins else None,
                int(median(maxs)) if maxs else None,
                currency,
                compact=True,
            ),
        }
        out["entries"] += bucket["count"]
    return out


# --------------------------------------------------------------------------
# Weekly throughput
# --------------------------------------------------------------------------


def weekly_activity(vault: Vault, today: date, weeks: int = 12) -> List[Dict[str, Any]]:
    """Applications submitted and interviews held, per ISO week.

    The comparison that matters is *input* (applications) against *outcome*
    (interviews).  A week with ten applications and no interviews is a signal
    about targeting, not effort.
    """

    weeks = max(1, min(int(weeks), 52))
    start = today - timedelta(days=today.weekday())  # Monday of this week
    out: List[Dict[str, Any]] = []

    for offset in range(weeks - 1, -1, -1):
        week_start = start - timedelta(days=7 * offset)
        week_end = week_start + timedelta(days=6)
        applied = [
            a
            for a in vault.applications
            if a.applied_on and week_start <= date.fromisoformat(a.applied_on) <= week_end
        ]
        interviews = [
            i
            for a in vault.applications
            for i in a.interviews
            if week_start <= i.date <= week_end
        ]
        closings = [
            a
            for a in vault.applications
            if a.closed_on and week_start <= date.fromisoformat(a.closed_on) <= week_end
        ]
        out.append(
            {
                "week_start": week_start.isoformat(),
                "week_end": week_end.isoformat(),
                "iso_week": f"{week_start.isocalendar()[0]}-W{week_start.isocalendar()[1]:02d}",
                "applied": len(applied),
                "interviews": len(interviews),
                "closed": len(closings),
                "label": f"{week_start.strftime('%b %d')}",
            }
        )
    return out


def streak(vault: Vault, today: date) -> Dict[str, Any]:
    """Consecutive weeks (up to and including this one) with >= 1 application."""

    counts: Dict[str, int] = {}
    for app in vault.applications:
        if not app.applied_on:
            continue
        stamp = date.fromisoformat(app.applied_on)
        key = f"{stamp.isocalendar()[0]}-W{stamp.isocalendar()[1]:02d}"
        counts[key] = counts.get(key, 0) + 1

    current = 0
    cursor = today - timedelta(days=today.weekday())
    while True:
        key = f"{cursor.isocalendar()[0]}-W{cursor.isocalendar()[1]:02d}"
        if counts.get(key, 0) > 0:
            current += 1
            cursor -= timedelta(days=7)
        else:
            break

    best = 0
    run = 0
    for row in weekly_activity(vault, today, weeks=52):
        if row["applied"] > 0:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return {"current_weeks": current, "longest_weeks": best, "weeks_logged": len(counts)}


# --------------------------------------------------------------------------
# Summary bundle
# --------------------------------------------------------------------------


def summary(vault: Vault, today: date) -> Dict[str, Any]:
    """Everything the report layer needs, in one deterministic bundle."""

    fun = funnel(vault, today)
    return {
        "today": today.isoformat(),
        "totals": {
            "applications": len(vault.applications),
            "submitted": fun["submitted"],
            "open": fun["open"],
            "wishlist": fun["wishlist"],
            "closed": fun["closed"],
        },
        "funnel": fun,
        "aging": aging_rows(vault, today),
        "stalled": stalled(vault, today),
        "time_in_stage": time_in_stage(vault, today),
        "sources": source_breakdown(vault, today),
        "salary": salary_summary(vault),
        "weekly": weekly_activity(vault, today, weeks=8),
        "streak": streak(vault, today),
        "status_counts": _status_counts(vault),
    }


def _status_counts(vault: Vault) -> Dict[str, int]:
    counts: Dict[str, int] = {status: 0 for status in STATUS_LABELS}
    for app in vault.applications:
        counts[app.status] = counts.get(app.status, 0) + 1
    return counts
