"""Persistence for FORGE.

A single JSON file holds everything: the athlete profile, the active program
and every logged workout. Plain JSON means the whole training history is
readable, diffable and greppable — you own your data and can back it up with
`cp`. Writes are atomic (temp file + rename) so an interrupted save can never
leave a half-written history behind.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Program, Workout

SCHEMA_VERSION = 1


def default_store_path() -> Path:
    """Where the training file lives.

    `FORGE_DATA` overrides everything; otherwise `~/.forge/training.json`.
    """
    env = os.environ.get("FORGE_DATA")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".forge" / "training.json"


class Store:
    """Read/write access to the training file."""

    def __init__(self, path: Optional[Any] = None):
        self.path = Path(path).expanduser() if path else default_store_path()
        self._data: Optional[Dict[str, Any]] = None

    # -- loading / saving ---------------------------------------------------

    def _blank(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "profile": {
                "name": "",
                "bodyweight_kg": 0.0,
                "units": "kg",
                "experience": "beginner",
                "goal": "strength",
                "equipment": [],
            },
            "program": None,
            "workouts": [],
        }

    def load(self) -> Dict[str, Any]:
        """Load the file, creating a blank structure if it is absent."""
        if self._data is not None:
            return self._data
        if not self.path.exists():
            self._data = self._blank()
            return self._data
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"{self.path} is not valid JSON ({exc}). "
                "Move it aside or fix it by hand — refusing to overwrite it."
            ) from exc
        if not isinstance(raw, dict):
            raise ValueError(f"{self.path} should contain a JSON object")
        data = self._blank()
        data.update(raw)
        data.setdefault("profile", {})
        data.setdefault("workouts", [])
        self._data = data
        return data

    def save(self, data: Optional[Dict[str, Any]] = None) -> Path:
        """Atomically write the training file, creating parent dirs as needed."""
        payload = data if data is not None else self.load()
        self._data = payload
        payload["schema_version"] = SCHEMA_VERSION
        payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)

        fd, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".training-", suffix=".json"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            # Never leave a stray temp file behind on failure.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return self.path

    def exists(self) -> bool:
        return self.path.exists()

    def reset(self) -> Path:
        """Wipe the file back to a blank state (used by `forge init`)."""
        return self.save(self._blank())

    # -- profile ------------------------------------------------------------

    @property
    def profile(self) -> Dict[str, Any]:
        return self.load().setdefault("profile", {})

    def set_profile(self, **fields: Any) -> Dict[str, Any]:
        prof = self.profile
        for key, value in fields.items():
            if value is not None:
                prof[key] = value
        self.save()
        return prof

    @property
    def bodyweight_kg(self) -> float:
        try:
            return float(self.profile.get("bodyweight_kg") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    @property
    def experience(self) -> str:
        return str(self.profile.get("experience") or "beginner")

    # -- program ------------------------------------------------------------

    def get_program(self) -> Optional[Program]:
        raw = self.load().get("program")
        if not raw:
            return None
        from .models import PlannedExercise, SessionPlan

        sessions = []
        for s in raw.get("sessions", []):
            sessions.append(
                SessionPlan(
                    day=s.get("day", ""),
                    title=s.get("title", ""),
                    focus=list(s.get("focus", [])),
                    exercises=[
                        PlannedExercise(**{k: v for k, v in e.items()})
                        for e in s.get("exercises", [])
                    ],
                )
            )
        return Program(
            name=raw.get("name", "Program"),
            goal=raw.get("goal", "strength"),
            experience=raw.get("experience", "beginner"),
            days_per_week=int(raw.get("days_per_week", len(sessions))),
            weeks=int(raw.get("weeks", 8)),
            split=raw.get("split", ""),
            bodyweight_kg=float(raw.get("bodyweight_kg", 0.0)),
            sessions=sessions,
            notes=list(raw.get("notes", [])),
        )

    def set_program(self, program: Program) -> Program:
        self.load()["program"] = program.to_dict()
        self.save()
        return program

    # -- workouts -----------------------------------------------------------

    @property
    def workouts(self) -> List[Workout]:
        return [Workout.from_dict(w) for w in self.load().get("workouts", [])]

    def add_workout(self, workout: Workout) -> Workout:
        """Insert a workout, keeping the list sorted by date.

        Same exercise logged twice in one session is merged into one entry so
        analytics never double-count a movement.
        """
        data = self.load()
        merged_target = None
        for existing in data["workouts"]:
            if str(existing.get("date", ""))[:10] == workout.date:
                merged_target = existing
                break

        if merged_target is None:
            data["workouts"].append(workout.to_dict())
        else:
            by_id = {e["exercise_id"]: e for e in merged_target.setdefault("entries", [])}
            for entry in workout.entries:
                if entry.exercise_id in by_id:
                    by_id[entry.exercise_id]["sets"].extend(
                        s.to_dict() for s in entry.sets
                    )
                else:
                    merged_target["entries"].append(entry.to_dict())
            if workout.session:
                merged_target["session"] = workout.session
            if workout.duration_min:
                merged_target["duration_min"] = workout.duration_min

        data["workouts"].sort(key=lambda w: str(w.get("date", "")))
        self.save()
        return workout

    def remove_workout(self, day: Any) -> bool:
        """Delete every workout on a given date. Returns True if anything went."""
        target = day.isoformat() if isinstance(day, date) else str(day)[:10]
        data = self.load()
        before = len(data["workouts"])
        data["workouts"] = [
            w for w in data["workouts"] if str(w.get("date", ""))[:10] != target
        ]
        removed = before != len(data["workouts"])
        if removed:
            self.save()
        return removed

    def workouts_in_range(self, start: Any, end: Any) -> List[Workout]:
        lo = start.isoformat() if isinstance(start, date) else str(start)[:10]
        hi = end.isoformat() if isinstance(end, date) else str(end)[:10]
        return [w for w in self.workouts if lo <= w.date <= hi]

    def last_workout(self, exercise_id: Optional[str] = None) -> Optional[Workout]:
        """Most recent workout, optionally the most recent containing a lift."""
        for w in reversed(self.workouts):
            if exercise_id is None or any(e.exercise_id == exercise_id for e in w.entries):
                return w
        return None

    def exercise_history(self, exercise_id: str) -> List[Dict[str, Any]]:
        """Chronological per-session summary for one lift.

        Each row: date, session, sets, top weight, best estimated 1RM and
        volume — the exact inputs the progression engine reasons over.
        """
        rows = []
        for w in self.workouts:
            for e in w.entries:
                if e.exercise_id != exercise_id or not e.sets:
                    continue
                top = e.top_set
                rows.append(
                    {
                        "date": w.date,
                        "session": w.session,
                        "sets": [s.to_dict() for s in e.sets],
                        "set_count": len(e.sets),
                        "top_weight": top.weight if top else 0.0,
                        "top_reps": top.reps if top else 0,
                        "best_e1rm": e.best_e1rm,
                        "volume": e.volume,
                        "total_reps": e.total_reps,
                    }
                )
        rows.sort(key=lambda r: r["date"])
        return rows
