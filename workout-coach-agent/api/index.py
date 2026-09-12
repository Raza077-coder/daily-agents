"""FORGE REST API (FastAPI) — Vercel-ready serverless entry point.

Wraps the same deterministic engine the CLI uses, so every endpoint returns
numbers that match `forge report` exactly. The training file lives in the OS
temp directory on serverless hosts, which means each instance is disposable —
fine for demos and for stateless analysis, not for durable logging.

Run locally:
    uvicorn api.index:app --reload --port 8000
"""

from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the sibling `forge` package importable when deployed as a serverless
# function, where the working directory is not the project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import HTMLResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from forge import __version__  # noqa: E402
from forge.analytics import summary as build_summary  # noqa: E402
from forge.coach import Coach  # noqa: E402
from forge.engine import ForgeEngine, parse_set_spec  # noqa: E402
from forge.exercises import ALL_EQUIPMENT, EXERCISES, filter_exercises, search_exercises  # noqa: E402
from forge.models import parse_date  # noqa: E402
from forge.program import SPLITS, build_program, project_overload  # noqa: E402
from forge.storage import Store  # noqa: E402

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

# Serverless instances are ephemeral: keep the store in temp unless the host
# provides a durable path via FORGE_DATA.
if not os.environ.get("FORGE_DATA"):
    os.environ["FORGE_DATA"] = str(Path(tempfile.gettempdir()) / "forge-api" / "training.json")

STORE = Store(os.environ["FORGE_DATA"])
ENGINE = ForgeEngine(STORE)
COACH = Coach()

app = FastAPI(
    title="FORGE — Workout Coach Agent API",
    version=__version__,
    description=(
        "Deterministic strength-training engine: exercise library, double-progression "
        "prescriptions, PR tracking and volume analytics. No ML, no external calls."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class InitRequest(BaseModel):
    bodyweight_kg: float = Field(0.0, ge=0, le=400)
    experience: str = "beginner"
    goal: str = "strength"
    equipment: List[str] = Field(default_factory=list)
    name: str = ""
    reset: bool = False


class LogRequest(BaseModel):
    exercise: str
    sets: str = Field(..., description="e.g. '60x8x3' or '60x8,60x8,60x6'")
    date: Optional[str] = None
    session: str = ""
    notes: str = ""
    duration_min: Optional[int] = None


class SessionRequest(BaseModel):
    entries: Dict[str, str] = Field(..., description="{exercise: set_spec}")
    date: Optional[str] = None
    session: str = ""


class ProgramRequest(BaseModel):
    days_per_week: int = Field(3, ge=2, le=6)
    experience: str = "beginner"
    goal: str = "strength"
    bodyweight_kg: float = Field(75.0, ge=0, le=400)
    equipment: List[str] = Field(default_factory=list)
    weeks: Optional[int] = None
    split: Optional[str] = None
    save: bool = True
    project_weeks: int = Field(0, ge=0, le=12)


# ---------------------------------------------------------------------------
# Meta
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def root() -> str:
    return _landing_html()


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "agent": "FORGE — Workout Coach",
        "version": __version__,
        "exercises": len(EXERCISES),
        "splits": sorted(SPLITS),
        "logged_workouts": len(STORE.workouts),
        "data_file": str(STORE.path),
        "disclaimer": COACH.disclaimer,
    }


@app.get("/api/exercises")
def exercises(
    q: Optional[str] = None,
    muscle: Optional[str] = None,
    equipment: Optional[str] = None,
    pattern: Optional[str] = None,
    limit: int = Query(60, ge=1, le=300),
) -> Dict[str, Any]:
    if q:
        rows = search_exercises(q)
        if muscle:
            rows = [e for e in rows if muscle in e.focus_muscles]
        if equipment:
            rows = [e for e in rows if e.equipment == equipment]
        if pattern:
            rows = [e for e in rows if e.pattern == pattern]
    else:
        rows = filter_exercises(
            equipment=[equipment] if equipment else None,
            muscle=muscle,
            pattern=pattern,
        )
    return {
        "count": len(rows),
        "equipment_options": ALL_EQUIPMENT,
        "exercises": [e.to_dict() for e in rows[:limit]],
    }


@app.get("/api/splits")
def splits() -> Dict[str, Any]:
    return {
        "splits": [
            {"id": k, "name": v["split"], "days": v["days"]}
            for k, v in sorted(SPLITS.items())
        ]
    }


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@app.post("/api/profile")
def set_profile(req: InitRequest) -> Dict[str, Any]:
    if req.experience not in ("beginner", "intermediate", "advanced"):
        raise HTTPException(422, "experience must be beginner, intermediate or advanced")
    if req.goal not in ("strength", "hypertrophy", "fat_loss", "general"):
        raise HTTPException(422, "goal must be strength, hypertrophy, fat_loss or general")
    bad = [e for e in req.equipment if e not in ALL_EQUIPMENT]
    if bad:
        raise HTTPException(422, f"unknown equipment: {', '.join(bad)}")
    profile = ENGINE.init_profile(
        bodyweight_kg=req.bodyweight_kg,
        experience=req.experience,
        goal=req.goal,
        equipment=req.equipment,
        name=req.name,
        reset=req.reset,
    )
    return {"profile": profile, "data_file": str(STORE.path)}


@app.get("/api/profile")
def get_profile() -> Dict[str, Any]:
    return {"profile": ENGINE.profile, "data_file": str(STORE.path)}


# ---------------------------------------------------------------------------
# Program
# ---------------------------------------------------------------------------


@app.post("/api/program")
def create_program(req: ProgramRequest) -> Dict[str, Any]:
    if req.split and req.split not in SPLITS:
        raise HTTPException(422, f"unknown split {req.split!r}")
    bad = [e for e in req.equipment if e not in ALL_EQUIPMENT]
    if bad:
        raise HTTPException(422, f"unknown equipment: {', '.join(bad)}")
    program = build_program(
        days_per_week=req.days_per_week,
        experience=req.experience,
        goal=req.goal,
        bodyweight_kg=req.bodyweight_kg,
        equipment=req.equipment or None,
        weeks=req.weeks,
        split=req.split,
    )
    if req.save:
        STORE.set_program(program)
    payload: Dict[str, Any] = {
        "program": program.to_dict(),
        "saved": req.save,
        "disclaimer": COACH.disclaimer,
    }
    if req.project_weeks:
        payload["projection"] = project_overload(program, req.project_weeks)
    return payload


@app.get("/api/program")
def get_program() -> Dict[str, Any]:
    program = STORE.get_program()
    if program is None:
        raise HTTPException(404, "no programme saved — POST /api/program first")
    return {"program": program.to_dict()}


@app.get("/api/program/projection")
def projection(weeks: int = Query(4, ge=1, le=12)) -> Dict[str, Any]:
    program = STORE.get_program()
    if program is None:
        raise HTTPException(404, "no programme saved — POST /api/program first")
    return {"program": program.name, "weeks": weeks, "projection": project_overload(program, weeks)}


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


@app.post("/api/log")
def log(req: LogRequest) -> Dict[str, Any]:
    try:
        parse_set_spec(req.sets)  # validate before touching the store
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        result = ENGINE.log(
            req.exercise,
            req.sets,
            day=req.date,
            session=req.session,
            notes=req.notes,
            duration_min=req.duration_min,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    result["next"] = ENGINE.next_prescription(result["exercise"])
    result["narrative"] = COACH.describe_prs(result["prs"])
    return result


@app.post("/api/session")
def log_session(req: SessionRequest) -> Dict[str, Any]:
    if not req.entries:
        raise HTTPException(422, "entries cannot be empty")
    try:
        result = ENGINE.log_session(day=req.date, session=req.session, entries=req.entries)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return result


@app.get("/api/today")
def today(day: Optional[str] = None) -> Dict[str, Any]:
    try:
        data = ENGINE.today(day)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"bad date: {exc}") from exc
    return data


@app.get("/api/history")
def history(limit: int = Query(20, ge=1, le=500)) -> Dict[str, Any]:
    workouts = STORE.workouts
    rows = workouts[-limit:]
    return {"count": len(workouts), "workouts": [w.to_dict() for w in rows]}


@app.delete("/api/workout/{day}")
def delete_day(day: str) -> Dict[str, Any]:
    try:
        target = parse_date(day)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"bad date {day!r}") from exc
    removed = ENGINE.remove_day(target)
    if not removed:
        raise HTTPException(404, f"nothing logged on {target.isoformat()}")
    return {"date": target.isoformat(), "removed": True}


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------


@app.get("/api/progress/{exercise}")
def progress(exercise: str) -> Dict[str, Any]:
    try:
        return ENGINE.progress(exercise)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/prs")
def prs(exercise: Optional[str] = None) -> Dict[str, Any]:
    try:
        rows = ENGINE.personal_records(exercise)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"count": len(rows), "records": rows}


@app.get("/api/next/{exercise}")
def next_prescription(
    exercise: str,
    rep_low: int = Query(8, ge=1, le=30),
    rep_high: int = Query(12, ge=1, le=50),
    sets: int = Query(3, ge=1, le=12),
) -> Dict[str, Any]:
    if rep_high <= rep_low:
        raise HTTPException(422, "rep_high must be greater than rep_low")
    try:
        return ENGINE.next_prescription(exercise, rep_low, rep_high, sets)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/report")
def report(days: int = Query(28, ge=1, le=365), day: Optional[str] = None) -> Dict[str, Any]:
    try:
        ref = parse_date(day) if day else date.today()
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"bad date: {exc}") from exc
    data = build_summary(STORE.workouts, ref, days)
    data["profile"] = ENGINE.profile
    data["disclaimer"] = COACH.disclaimer
    return data


@app.get("/api/report/text", response_class=HTMLResponse)
def report_text(days: int = Query(28, ge=1, le=365)) -> str:
    data = build_summary(STORE.workouts, date.today(), days)
    body = COACH.describe_summary(data)
    return f"<pre style='font:13px ui-monospace,monospace;padding:20px'>{_escape(body)}</pre>"


@app.post("/api/demo")
def demo(force: bool = True) -> Dict[str, Any]:
    """Seed a realistic 3-week history. Idempotent when force=True."""
    if force:
        STORE.reset()
    ENGINE.init_profile(bodyweight_kg=80.0, experience="intermediate", goal="strength")
    STORE.set_program(
        build_program(days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80.0)
    )
    today_d = date.today()
    plan = [
        (20, "Upper A", {"barbell_bench_press": "60x8x3", "barbell_row": "50x10x3",
                         "overhead_press": "35x8x3", "lat_pulldown": "50x10x3"}),
        (18, "Lower A", {"back_squat": "70x8x3", "romanian_deadlift": "60x8x3",
                         "leg_curl": "30x12x3", "calf_raise": "50x15x3"}),
        (15, "Upper A", {"barbell_bench_press": "62.5x8x3", "barbell_row": "52.5x10x3",
                         "overhead_press": "37.5x8x3", "lat_pulldown": "52.5x10x3"}),
        (13, "Lower A", {"back_squat": "75x8x3", "romanian_deadlift": "62.5x8x3",
                         "leg_curl": "32.5x12x3", "calf_raise": "52.5x15x3"}),
        (8, "Upper A", {"barbell_bench_press": "65x10x3", "barbell_row": "55x10x3",
                        "overhead_press": "40x8x3", "lat_pulldown": "55x10x3"}),
        (6, "Lower A", {"back_squat": "80x8x3", "romanian_deadlift": "65x8x3",
                        "leg_curl": "35x12x3", "calf_raise": "55x15x3"}),
        (3, "Upper A", {"barbell_bench_press": "67.5x8x3", "barbell_row": "57.5x10x3",
                        "overhead_press": "40x10x3", "lat_pulldown": "57.5x10x3"}),
        (1, "Lower A", {"back_squat": "82.5x8x3", "romanian_deadlift": "67.5x8x3",
                        "leg_curl": "37.5x12x3", "calf_raise": "57.5x15x3"}),
    ]
    for days_ago, session, entries in plan:
        ENGINE.log_session(day=today_d - timedelta(days=days_ago), session=session, entries=entries)
    data = build_summary(STORE.workouts, today_d, 28)
    return {
        "seeded_sessions": len(plan),
        "seeded_exercises": sum(len(e) for _, _, e in plan),
        "summary": data,
    }


@app.get("/api/export")
def export() -> Dict[str, Any]:
    """Everything stored, so the data is never trapped in the serverless sandbox."""
    return STORE.load()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _landing_html() -> str:
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FORGE — Workout Coach API</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; background:#0b0f14; color:#e6edf3;
         font:15px/1.65 ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:48px 24px 72px; }}
  h1 {{ font-size:30px; margin:0 0 6px; letter-spacing:-0.02em; }}
  .sub {{ color:#8b98a5; margin-bottom:28px; }}
  .badge {{ display:inline-block; padding:3px 10px; border-radius:999px;
            background:#12202b; border:1px solid #1e3745; color:#4fd1c5;
            font:600 12px ui-monospace,monospace; }}
  table {{ width:100%; border-collapse:collapse; margin:18px 0 28px; }}
  td, th {{ text-align:left; padding:9px 12px; border-bottom:1px solid #1b2630; }}
  th {{ color:#8b98a5; font-weight:600; font-size:12px; text-transform:uppercase;
        letter-spacing:.06em; }}
  code {{ background:#111a22; padding:2px 7px; border-radius:5px;
          font:13px ui-monospace,monospace; color:#7ee787; }}
  a {{ color:#4fd1c5; }}
  .note {{ margin-top:26px; padding:14px 18px; border-left:3px solid #d29922;
           background:#1a1509; color:#e8d5a3; border-radius:0 6px 6px 0; font-size:14px; }}
</style></head><body><div class="wrap">
<h1>FORGE — Workout Coach Agent</h1>
<div class="sub">Deterministic strength programming, logging and volume analytics.
<span class="badge">v{__version__}</span></div>
<p>The API is live. Try the endpoints below, or read the interactive docs at
<a href="/docs">/docs</a>.</p>
<table>
<tr><th>Method</th><th>Endpoint</th><th>What it does</th></tr>
<tr><td>GET</td><td><code>/api/health</code></td><td>Status, library size, disclaimer</td></tr>
<tr><td>GET</td><td><code>/api/exercises?muscle=chest</code></td><td>Search the {len(EXERCISES)}-movement library</td></tr>
<tr><td>POST</td><td><code>/api/program</code></td><td>Build a weekly split (days, goal, equipment)</td></tr>
<tr><td>GET</td><td><code>/api/today</code></td><td>Today's session with per-lift advice</td></tr>
<tr><td>POST</td><td><code>/api/log</code></td><td>Log sets, e.g. <code>"sets": "60x8x3"</code></td></tr>
<tr><td>GET</td><td><code>/api/report?days=28</code></td><td>Volume, balance, streaks, plateaus</td></tr>
<tr><td>POST</td><td><code>/api/demo</code></td><td>Seed a 3-week history to explore</td></tr>
</table>
<div class="note"><strong>Ephemeral storage:</strong> serverless instances keep the
training file in temp storage. Data does not persist between cold starts — use
<code>GET /api/export</code> to pull it out, or run the CLI locally for durable
logging. <br><br>{_escape(COACH.disclaimer)}</div>
</div></body></html>"""


# Vercel's Python runtime looks for a module-level `app`.
__all__ = ["app"]
