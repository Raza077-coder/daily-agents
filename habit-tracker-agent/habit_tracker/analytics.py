"""Streak + analytics computation for HABITOS.

Pure, deterministic functions over habit logs. No I/O here — pass in the
log dates and target frequency and get numbers back.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Iterable, List, Optional, Set

from .storage import daterange, iso_week_bounds

# Calendar habit frequencies expressed in "days per week" semantics.
FREQ_LABELS = {
    "daily": 7,
    "weekdays": 5,
    "weekly": 1,
    "weekends": 2,
}


def normalize_target(target: int, frequency: str = "") -> int:
    """Resolve a target_per_week from an explicit int or frequency label."""
    if frequency:
        freq = frequency.lower().strip()
        if freq in FREQ_LABELS:
            return FREQ_LABELS[freq]
    if isinstance(target, int) and target > 0:
        return min(target, 7)
    return 5


def compute_streaks(done_dates: Iterable[str], today: dt.date) -> Dict[str, int]:
    """Current and longest streak in days from a set of YYYY-MM-DD dates.

    Streak semantics: consecutive days (calendar days) on which the habit
    was done, measured back from today. Today does NOT need to be done yet:
    a run ending yesterday still counts as current while today is pending.
    """
    done: Set[dt.date] = {dt.date.fromisoformat(d) for d in done_dates if d}

    # Current streak
    current = 0
    cursor = today
    if cursor not in done and cursor - dt.timedelta(days=1) in done:
        cursor = cursor - dt.timedelta(days=1)
    while cursor in done:
        current += 1
        cursor = cursor - dt.timedelta(days=1)

    # Longest streak
    longest = 0
    run = 0
    day = min(done) if done else today
    end = max(done) if done else today
    d = day
    while d <= end:
        if d in done:
            run += 1
            longest = max(longest, run)
        else:
            run = 0
        d += dt.timedelta(days=1)
    return {"current": current, "longest": longest}


def week_stats(done_dates: Iterable[str], target: int, today: dt.date) -> Dict[str, float]:
    """Completions / possible days / completion % for the current ISO week."""
    done = {dt.date.fromisoformat(d) for d in done_dates if d}
    monday = iso_week_bounds(today)
    days_elapsed = (today - monday).days + 1
    week_done = sum(1 for d in done if monday <= d <= today)
    possible = min(days_elapsed, 7)
    pct = (week_done / possible * 100.0) if possible else 0.0
    pace = (week_done / days_elapsed * 7.0) if days_elapsed else 0.0
    return {
        "week_done": float(week_done),
        "week_possible": float(possible),
        "completion_pct": round(pct, 1),
        "pace_per_week": round(pace, 1),
        "target": float(target),
    }


def status_for(pct: float, week_done: int, target: int) -> str:
    """Label the habit's health this week."""
    if pct >= 80:
        return "on_track"
    if week_done >= max(1, target - 1):
        return "on_track"
    if pct >= 50:
        return "at_risk"
    return "off_track"


def log_map(logs) -> Dict[str, Set[str]]:
    """habit_id -> set of date strings done."""
    m: Dict[str, Set[str]] = {}
    for l in logs:
        m.setdefault(l.habit_id, set()).add(l.date)
    return m
