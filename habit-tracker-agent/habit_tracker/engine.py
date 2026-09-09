"""HABITOS engine — orchestration layer over storage + analytics.

The engine exposes the full feature set of the Habit Tracker Agent:
manage habits, log completions (with backfill), compute streaks and
weekly reports, and render text/markdown summaries. Deterministic and
offline. Safe to run multiple times — idempotent operations only.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from .analytics import compute_streaks, log_map, status_for, week_stats
from .models import Habit, LogEntry, today_str
from .storage import (dehydrate, hydrate, iso_week_bounds, load_data,
                      parse_date, save_data, slugify, daterange)

CATEGORIES = ["health", "productivity", "learning", "fitness", "mindfulness", "general"]
DEFAULT_COLORS = {
    "health": "#00e5ff",
    "productivity": "#7c4dff",
    "learning": "#00e676",
    "fitness": "#ff5252",
    "mindfulness": "#ffab40",
    "general": "#40c4ff",
}


class HabitEngine:
    """Main engine class.

    :param data_path: JSON file path. None -> temp file (default).
                      "" -> pure in-memory (no persistence).
    """

    def __init__(self, data_path: Optional[str] = None):
        self.data_path = data_path
        self._raw = load_data(data_path)
        self.store = hydrate(self._raw)
        self.today = dt.date.today()

    # ------------------------------------------------------------------ #
    # Habit CRUD
    # ------------------------------------------------------------------ #
    def add_habit(self, name: str, category: str = "general",
                  target_per_week: Optional[int] = None,
                  frequency: str = "", color: str = "") -> Habit:
        name = (name or "").strip()
        if not name:
            raise ValueError("habit name is required")
        if category not in CATEGORIES:
            category = "general"
        hid = slugify(name)
        base = hid
        n = 2
        while hid in self.store.habits:
            hid = f"{base}-{n}"
            n += 1
        from .analytics import normalize_target
        target = normalize_target(target_per_week or 5, frequency)
        habit = Habit(
            name=name,
            habit_id=hid,
            category=category,
            target_per_week=target,
            color=color or DEFAULT_COLORS.get(category, DEFAULT_COLORS["general"]),
            created=today_str(),
        )
        self.store.habits[hid] = habit
        self._persist()
        return habit

    def get_habit(self, habit_id: str) -> Optional[Habit]:
        # Accept either id or exact/partial name match.
        if habit_id in self.store.habits:
            return self.store.habits[habit_id]
        low = habit_id.lower()
        for h in self.store.habits.values():
            if h.name.lower() == low or slugify(h.name) == slugify(habit_id):
                return h
        return None

    def list_habits(self, include_archived: bool = False) -> List[Habit]:
        habits = [h for h in self.store.habits.values()
                  if include_archived or not h.archived]
        habits.sort(key=lambda h: (h.archived, h.created, h.name))
        self.refresh_metrics(habits)
        return habits

    def delete_habit(self, habit_id: str) -> bool:
        h = self.get_habit(habit_id)
        if h is None:
            return False
        del self.store.habits[h.habit_id]
        self.store.logs = [l for l in self.store.logs if l.habit_id != h.habit_id]
        self._persist()
        return True

    def archive_habit(self, habit_id: str, archived: bool = True) -> Optional[Habit]:
        h = self.get_habit(habit_id)
        if h is None:
            return None
        h.archived = archived
        self._persist()
        return h

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    def log(self, habit_id: str, date: Optional[str] = None, note: str = "") -> LogEntry:
        """Mark a habit as done on a date (default today). Idempotent."""
        h = self.get_habit(habit_id)
        if h is None:
            raise KeyError(f"unknown habit: {habit_id}")
        d = (date or today_str()).strip()
        try:
            parse_date(d)
        except ValueError:
            raise ValueError(f"invalid date: {date!r} (expected YYYY-MM-DD)")
        # idempotent: remove existing entry first
        self.store.logs = [l for l in self.store.logs
                           if not (l.habit_id == h.habit_id and l.date == d)]
        entry = LogEntry(habit_id=h.habit_id, date=d, note=(note or "").strip())
        self.store.logs.append(entry)
        self._persist()
        return entry

    def unlog(self, habit_id: str, date: Optional[str] = None) -> bool:
        h = self.get_habit(habit_id)
        if h is None:
            return False
        d = date or today_str()
        before = len(self.store.logs)
        self.store.logs = [l for l in self.store.logs
                           if not (l.habit_id == h.habit_id and l.date == d)]
        changed = len(self.store.logs) != before
        if changed:
            self._persist()
        return changed

    def done_on(self, habit_id: str, date: str) -> bool:
        return any(l.habit_id == habit_id and l.date == date for l in self.store.logs)

    # ------------------------------------------------------------------ #
    # Metrics / refresh
    # ------------------------------------------------------------------ #
    def refresh_metrics(self, habits: Optional[List[Habit]] = None) -> None:
        """Recompute derived metrics on the given habits (default: all)."""
        if habits is None:
            habits = list(self.store.habits.values())
        m = log_map(self.store.logs)
        for h in habits:
            dates = m.get(h.habit_id, set())
            streaks = compute_streaks(dates, self.today)
            w = week_stats(dates, h.target_per_week, self.today)
            h.current_streak = streaks["current"]
            h.longest_streak = streaks["longest"]
            h.completion_pct = w["completion_pct"]
            h.week_done = int(w["week_done"])
            h.week_possible = int(w["week_possible"])
            h.total_done = len(dates)
            h.status = status_for(w["completion_pct"], h.week_done, h.target_per_week)

    def summary(self) -> Dict[str, Any]:
        habits = self.list_habits(include_archived=False)
        active = [h for h in habits if not h.archived]
        on_track = sum(1 for h in active if h.status == "on_track")
        at_risk = sum(1 for h in active if h.status == "at_risk")
        off = sum(1 for h in active if h.status == "off_track")
        total_logs = len(self.store.logs)
        days = sorted({l.date for l in self.store.logs})
        monday = iso_week_bounds(self.today)
        week_logs = [l for l in self.store.logs if monday <= parse_date(l.date) <= self.today]
        return {
            "active_habits": len(active),
            "total_habits": len(habits),
            "total_logs": total_logs,
            "week_logs": len(week_logs),
            "on_track": on_track,
            "at_risk": at_risk,
            "off_track": off,
            "span_days": (parse_date(days[-1]) - parse_date(days[0])).days + 1 if len(days) >= 2 else (1 if days else 0),
            "first_log": days[0] if days else None,
            "last_log": days[-1] if days else None,
        }

    def habit_detail(self, habit_id: str) -> Optional[Dict[str, Any]]:
        h = self.get_habit(habit_id)
        if h is None:
            return None
        self.refresh_metrics([h])
        dates = sorted(l.date for l in self.store.logs if l.habit_id == h.habit_id)
        monday = iso_week_bounds(self.today)
        last_7 = []
        for offset in range(6, -1, -1):
            d = self.today - dt.timedelta(days=offset)
            last_7.append({"date": d.isoformat(), "done": d.isoformat() in set(dates)})
        detail = h.to_dict()
        detail["log_dates"] = dates
        detail["last_7_days"] = last_7
        detail["week_monday"] = monday.isoformat()
        return detail

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #
    def weekly_report(self, format: str = "text") -> Any:
        """Human-readable weekly digest of every active habit."""
        habits = [h for h in self.store.habits.values() if not h.archived]
        habits.sort(key=lambda h: h.name)
        self.refresh_metrics(habits)
        m = log_map(self.store.logs)
        lines = []
        lines.append(f"HABITOS Weekly Report — week of {iso_week_bounds(self.today).isoformat()}")
        lines.append(f"Generated {today_str()} · {len(habits)} active habit(s)")
        lines.append("-" * 60)
        for h in habits:
            dates = m.get(h.habit_id, set())
            days_this_week = [d for d in dates
                              if iso_week_bounds(self.today) <= parse_date(d) <= self.today]
            dots = "".join("X" if d.isoformat() in set(days_this_week) else "·"
                           for d in daterange(iso_week_bounds(self.today), self.today))
            lines.append(
                f"[{h.status.upper():8s}] {h.name} "
                f"({h.week_done}/{h.target_per_week} · {h.completion_pct:.0f}% · "
                f"streak {h.current_streak}d / best {h.longest_streak}d)"
            )
            lines.append(f"          this week: {dots}")
        s = self.summary()
        lines.append("-" * 60)
        lines.append(f"On track {s['on_track']} · At risk {s['at_risk']} · Off track {s['off_track']}")
        if s["at_risk"] + s["off_track"] > 0:
            lines.append("Tip: log at least one habit before the day ends to protect your streaks.")
        else:
            lines.append("Excellent discipline, Sir. Every habit is on track.")
        text = "\n".join(lines)
        if format == "json":
            return {"summary": s, "habits": [h.to_dict() for h in habits]}
        return text

    def habit_trend(self, habit_id: str, weeks: int = 6) -> Optional[Dict[str, Any]]:
        """Per-week completion counts over the last N ISO weeks."""
        h = self.get_habit(habit_id)
        if h is None:
            return None
        monday = iso_week_bounds(self.today)
        done = {parse_date(l.date) for l in self.store.logs if l.habit_id == h.habit_id}
        buckets = []
        for back in range(weeks - 1, -1, -1):
            start = monday - dt.timedelta(days=7 * back)
            count = sum(1 for d in done if start <= d < start + dt.timedelta(days=7))
            buckets.append({"week_start": start.isoformat(), "count": count})
        return {"habit": h.to_dict(), "buckets": buckets}

    # ------------------------------------------------------------------ #
    # Persistence helpers
    # ------------------------------------------------------------------ #
    def _persist(self) -> None:
        if self.data_path == "":
            return
        save_data(dehydrate(self.store), self.data_path)

    def raw(self) -> Dict[str, Any]:
        return self.store.to_dict()
