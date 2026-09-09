"""FastAPI wrapper for HABITOS (Vercel-ready serverless entry).

Exposes the Habit Tracker Agent engine as a small REST API.
Run locally:  uvicorn api.index:app --reload
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

from habit_tracker.engine import HabitEngine

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
WEB_DIR = HERE.parent / "web-live"


class HabitIn(BaseModel):
    name: str
    category: str = "general"
    target_per_week: Optional[int] = None
    frequency: str = ""


class LogIn(BaseModel):
    habit: str = ""
    date: Optional[str] = None
    note: str = ""


class UnlogIn(BaseModel):
    habit: str = ""
    date: Optional[str] = None


class ArchiveIn(BaseModel):
    habit: str = ""
    archived: bool = True


def _data_path() -> str:
    return os.environ.get("HABITOS_DATA", str(Path(tempfile.gettempdir()) / "habitos_server.json"))


def _engine() -> HabitEngine:
    return HabitEngine(data_path=_data_path())


app = FastAPI(title="HABITOS — Habit Tracker Agent", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "agent": "habitos", "version": "1.0.0"}


@app.get("/routes")
def routes() -> Dict[str, Any]:
    return {
        "get": ["/health", "/routes", "/habits", "/habits/{id}", "/summary", "/report", "/report/json", "/web/"],
        "post": ["/habits", "/habits/{id}/log", "/habits/{id}/unlog", "/habits/{id}/archive"],
    }


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    f = WEB_DIR / "index.html"
    if f.exists():
        return HTMLResponse(f.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>HABITOS — Habit Tracker Agent</h1><p>Install the web demo under web-live/ to see the dashboard.</p>")


@app.get("/habits")
def list_habits(include_archived: bool = False) -> Dict[str, Any]:
    eng = _engine()
    habits = [h.to_dict() for h in eng.list_habits(include_archived=include_archived)]
    return {"habits": habits, "summary": eng.summary()}


@app.get("/habits/{habit_id}")
def get_habit(habit_id: str) -> Dict[str, Any]:
    d = _engine().habit_detail(habit_id)
    if d is None:
        raise HTTPException(404, f"unknown habit: {habit_id}")
    return d


@app.get("/summary")
def summary() -> Dict[str, Any]:
    return _engine().summary()


@app.get("/report")
def report_text() -> Dict[str, str]:
    return {"report": _engine().weekly_report(format="text")}


@app.get("/report/json")
def report_json() -> Dict[str, Any]:
    return _engine().weekly_report(format="json")


@app.post("/habits")
def create_habit(body: HabitIn) -> Dict[str, Any]:
    try:
        h = _engine().add_habit(body.name, category=body.category, target_per_week=body.target_per_week, frequency=body.frequency)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return h.to_dict()


@app.post("/habits/{habit_id}/log")
def log_habit(habit_id: str, body: LogIn | None = None) -> Dict[str, Any]:
    body = body or LogIn()
    target = body.habit or habit_id
    eng = _engine()
    try:
        e = eng.log(target, date=body.date, note=body.note)
        h = eng.get_habit(target)
        eng.refresh_metrics([h])
        return {"logged": e.to_dict(), "habit": h.to_dict()}
    except KeyError as ex:
        raise HTTPException(404, str(ex))
    except ValueError as ex:
        raise HTTPException(400, str(ex))


@app.post("/habits/{habit_id}/unlog")
def unlog_habit(habit_id: str, body: UnlogIn | None = None) -> Dict[str, Any]:
    body = body or UnlogIn()
    target = body.habit or habit_id
    changed = _engine().unlog(target, date=body.date)
    return {"removed": changed}


@app.post("/habits/{habit_id}/archive")
def archive_habit(habit_id: str, body: ArchiveIn | None = None) -> Dict[str, Any]:
    body = body or ArchiveIn()
    target = body.habit or habit_id
    h = _engine().archive_habit(target, archived=body.archived)
    if h is None:
        raise HTTPException(404, f"unknown habit: {target}")
    return h.to_dict()


@app.post("/demo")
def seed_demo() -> Dict[str, Any]:
    import datetime as dt
    import random
    eng = _engine()
    for h in list(eng.store.habits.values()):
        eng.delete_habit(h.habit_id)
    seeds = [("Meditate", "mindfulness", 7), ("Read 20 pages", "learning", 5), ("Workout", "fitness", 4), ("Ship code", "productivity", 3)]
    rng = random.Random(42)
    ids = [eng.add_habit(n, category=c, target_per_week=t).habit_id for n, c, t in seeds]
    today = dt.date.today()
    for hid, (name, cat, target) in zip(ids, seeds):
        for back in range(20, -1, -1):
            d = today - dt.timedelta(days=back)
            if cat == "fitness" and d.weekday() >= 5 and rng.random() < 0.7:
                continue
            if rng.random() < (target / 7.0) * 0.9:
                eng.log(hid, date=d.isoformat(), note="demo")
    return {"habits": [h.to_dict() for h in eng.list_habits()], "summary": eng.summary()}


@app.get("/web", response_class=HTMLResponse)
@app.get("/web/", response_class=HTMLResponse)
def web_demo() -> HTMLResponse:
    f = WEB_DIR / "index.html"
    if f.exists():
        return HTMLResponse(f.read_text(encoding="utf-8"))
    raise HTTPException(404, "web-live/index.html not found")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("api.index:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
