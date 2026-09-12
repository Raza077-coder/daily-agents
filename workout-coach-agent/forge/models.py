"""Data models for FORGE.

Everything is a plain dataclass so it serialises to JSON cleanly and stays
stable across versions. Dates are ISO `YYYY-MM-DD` strings throughout — this
keeps sorting, bucketing and JSON round-tripping trivial and timezone-free.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date as _date, datetime, timedelta
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Strength maths
# ---------------------------------------------------------------------------


def epley_e1rm(weight: float, reps: int) -> float:
    """Estimated one-rep max (Epley).

    Using an estimate rather than a tested 1RM means we can track strength
    progress from ordinary working sets without ever asking anyone to attempt a
    maximal single.
    """
    if weight <= 0 or reps <= 0:
        return 0.0
    if reps == 1:
        return float(weight)
    return round(weight * (1.0 + reps / 30.0), 1)


def parse_date(value: Any) -> _date:
    """Accept a date, datetime or ISO string and return a `date`."""
    if isinstance(value, _date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        return _date.fromisoformat(value[:10])
    raise TypeError(f"cannot interpret {value!r} as a date")


# ---------------------------------------------------------------------------
# Library
# ---------------------------------------------------------------------------


@dataclass
class Exercise:
    """One movement in the library.

    `strength_ratio` is roughly how much an *intermediate* lifter moves for
    8-10 good reps, expressed as a fraction of bodyweight. It is used only to
    pick a sane starting load — it is never a target or a judgement.
    """

    id: str
    name: str
    primary: str
    secondary: List[str] = field(default_factory=list)
    equipment: str = "barbell"
    pattern: str = "horizontal_push"
    kind: str = "compound"
    level: str = "beginner"
    strength_ratio: float = 0.0
    increment: float = 2.5
    unilateral: bool = False

    @property
    def is_bodyweight(self) -> bool:
        return self.equipment == "bodyweight"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Exercise":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in known})


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@dataclass
class SetEntry:
    """A single working set.

    For bodyweight movements `weight` is *added* load (a belt or vest), so a
    plain set of pull-ups is `weight=0`.
    """

    weight: float
    reps: int
    rpe: Optional[float] = None

    @property
    def volume(self) -> float:
        return round(float(self.weight) * int(self.reps), 1)

    @property
    def e1rm(self) -> float:
        return epley_e1rm(self.weight, self.reps)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"weight": self.weight, "reps": self.reps}
        if self.rpe is not None:
            out["rpe"] = self.rpe
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SetEntry":
        return cls(
            weight=float(raw.get("weight", 0) or 0),
            reps=int(raw.get("reps", 0) or 0),
            rpe=raw.get("rpe"),
        )


@dataclass
class LoggedExercise:
    exercise_id: str
    sets: List[SetEntry] = field(default_factory=list)
    notes: str = ""

    @property
    def volume(self) -> float:
        return round(sum(s.volume for s in self.sets), 1)

    @property
    def total_reps(self) -> int:
        return sum(int(s.reps) for s in self.sets)

    @property
    def top_set(self) -> Optional[SetEntry]:
        """The heaviest set, breaking ties on reps then estimated 1RM."""
        if not self.sets:
            return None
        return max(self.sets, key=lambda s: (s.weight, s.reps, s.e1rm))

    @property
    def best_e1rm(self) -> float:
        return max((s.e1rm for s in self.sets), default=0.0)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "exercise_id": self.exercise_id,
            "sets": [s.to_dict() for s in self.sets],
        }
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "LoggedExercise":
        return cls(
            exercise_id=raw["exercise_id"],
            sets=[SetEntry.from_dict(s) for s in raw.get("sets", [])],
            notes=raw.get("notes", "") or "",
        )


@dataclass
class Workout:
    date: str
    session: str = ""
    entries: List[LoggedExercise] = field(default_factory=list)
    duration_min: Optional[int] = None
    rpe: Optional[float] = None
    notes: str = ""

    @property
    def volume(self) -> float:
        return round(sum(e.volume for e in self.entries), 1)

    @property
    def total_sets(self) -> int:
        return sum(len(e.sets) for e in self.entries)

    @property
    def total_reps(self) -> int:
        return sum(e.total_reps for e in self.entries)

    @property
    def day(self) -> _date:
        return parse_date(self.date)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "date": self.date,
            "session": self.session,
            "entries": [e.to_dict() for e in self.entries],
        }
        if self.duration_min is not None:
            out["duration_min"] = self.duration_min
        if self.rpe is not None:
            out["rpe"] = self.rpe
        if self.notes:
            out["notes"] = self.notes
        return out

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Workout":
        return cls(
            date=str(raw["date"])[:10],
            session=raw.get("session", "") or "",
            entries=[LoggedExercise.from_dict(e) for e in raw.get("entries", [])],
            duration_min=raw.get("duration_min"),
            rpe=raw.get("rpe"),
            notes=raw.get("notes", "") or "",
        )


# ---------------------------------------------------------------------------
# Programming
# ---------------------------------------------------------------------------


@dataclass
class PlannedExercise:
    exercise_id: str
    sets: int
    rep_low: int
    rep_high: int
    start_weight: float
    increment: float
    rest_sec: int = 120

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SessionPlan:
    day: str
    title: str
    focus: List[str] = field(default_factory=list)
    exercises: List[PlannedExercise] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "day": self.day,
            "title": self.title,
            "focus": list(self.focus),
            "exercises": [e.to_dict() for e in self.exercises],
        }


@dataclass
class Program:
    name: str
    goal: str
    experience: str
    days_per_week: int
    weeks: int
    split: str
    bodyweight_kg: float
    sessions: List[SessionPlan] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def training_days(self) -> List[str]:
        return [s.day for s in self.sessions]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "goal": self.goal,
            "experience": self.experience,
            "days_per_week": self.days_per_week,
            "weeks": self.weeks,
            "split": self.split,
            "bodyweight_kg": self.bodyweight_kg,
            "sessions": [s.to_dict() for s in self.sessions],
            "notes": list(self.notes),
        }
