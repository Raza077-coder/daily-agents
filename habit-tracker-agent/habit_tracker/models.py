"""Core data models for HABITOS."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


def today_str() -> str:
    """Local today as YYYY-MM-DD."""
    return dt.date.today().isoformat()


@dataclass
class Habit:
    """A single habit being tracked."""

    name: str
    habit_id: str = ""
    category: str = "general"          # health, productivity, learning, fitness, mindfulness, general
    target_per_week: int = 5           # goal: days per week
    color: str = "#00e5ff"
    created: str = ""
    archived: bool = False
    # Derived metrics (recomputed on each report/status run)
    current_streak: int = 0
    longest_streak: int = 0
    completion_pct: float = 0.0        # this week so far (0-100)
    total_done: int = 0
    week_done: int = 0                 # completions this ISO week
    week_possible: int = 0             # days elapsed this ISO week
    status: str = "on_track"           # on_track | at_risk | off_track | archived

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LogEntry:
    """One completion record for a habit on a date."""

    habit_id: str
    date: str                     # YYYY-MM-DD
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class HabitStore:
    """In-memory store mirroring the JSON persistence layout."""

    habits: Dict[str, Habit] = field(default_factory=dict)
    logs: List[LogEntry] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "habits": [h.to_dict() for h in self.habits.values()],
            "logs": [l.to_dict() for l in self.logs],
        }
