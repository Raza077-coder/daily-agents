"""Date + JSON persistence helpers for HABITOS."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Habit, LogEntry

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def iso_week_bounds(ref: dt.date) -> dt.date:
    """Monday of the ISO week containing ref."""
    return ref - dt.timedelta(days=ref.weekday())


def parse_date(value: str) -> dt.date:
    return dt.date.fromisoformat(value)


def slugify(text: str) -> str:
    """Lowercase alnum id from arbitrary text."""
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "habit"


def daterange(start: dt.date, end: dt.date):
    cur = start
    while cur <= end:
        yield cur
        cur += dt.timedelta(days=1)


def load_data(path: Optional[str] = None) -> Dict[str, Any]:
    """Load store dict from JSON file; empty store when missing/invalid."""
    p = _resolve(path)
    if p is not None and p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "habits" in data:
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {"habits": [], "logs": []}


def save_data(data: Dict[str, Any], path: Optional[str] = None) -> str:
    p = _resolve(path)
    if p is None:
        return ""  # in-memory only
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return str(p)


def _resolve(path: Optional[str]) -> Optional[Path]:
    if path is None:
        env = os.environ.get("HABITOS_DATA")
        if env:
            return Path(env)
        return Path(tempfile.gettempdir()) / "habitos_data.json"
    return Path(path)


def hydrate(data: Dict[str, Any]) -> "HabitStore":
    """Rebuild a HabitStore from a raw dict (habits list + logs list)."""
    from .models import HabitStore

    store = HabitStore()
    for h in data.get("habits", []):
        if isinstance(h, dict) and "name" in h:
            store.habits[h.get("habit_id") or slugify(h.get("name", ""))] = Habit(**{
                k: v for k, v in h.items() if k in Habit.__dataclass_fields__
            })
    for l in data.get("logs", []):
        if isinstance(l, dict) and "habit_id" in l and "date" in l:
            store.logs.append(LogEntry(habit_id=l["habit_id"], date=l["date"], note=l.get("note", "")))
    return store


def dehydrate(store: "HabitStore") -> Dict[str, Any]:
    return store.to_dict()
