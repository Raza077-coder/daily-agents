"""JOBFLOW REST layer — a thin FastAPI wrapper over :class:`JobFlowEngine`.

The API holds **no business logic**.  Every endpoint resolves a vault, calls
one engine method and serialises the result, so the REST surface can never
disagree with the CLI.

Storage note for serverless deploys
-----------------------------------
The vault is a file.  On Vercel (or any ephemeral filesystem) that file lives
only as long as the instance, so writes do not persist across cold starts.
For a durable deployment point ``JOBFLOW_VAULT`` at a mounted volume, or run
the CLI locally where the file is real.  ``GET /api/export`` exists so a
serverless caller can always retrieve the full state.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from jobtracker import TAGLINE, __version__, report
from jobtracker.engine import JobFlowEngine
from jobtracker.models import JobFlowError, NotFoundError, ValidationError

app = FastAPI(
    title="JOBFLOW",
    version=__version__,
    description=TAGLINE,
)

# The browser demo is a separate static page; allow it to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------
# Vault plumbing
# --------------------------------------------------------------------------


def vault_path() -> Optional[Path]:
    """Where this instance keeps its vault.

    ``JOBFLOW_VAULT`` wins so a deployment can mount a real volume.  Otherwise
    fall back to a writable temp location, because a serverless bundle is
    read-only and the default ``cwd`` is not writable there.
    """

    env = os.environ.get("JOBFLOW_VAULT")
    if env:
        return Path(env)
    return Path(os.environ.get("JOBFLOW_DATA_DIR", "/tmp")) / "jobflow-api.json"


def get_engine() -> JobFlowEngine:
    return JobFlowEngine.open(vault_path())


def _fail(exc: Exception) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, JobFlowError):
        return HTTPException(status_code=409, detail=str(exc))
    return HTTPException(status_code=500, detail=f"unexpected error: {exc}")


# --------------------------------------------------------------------------
# Schemas
# --------------------------------------------------------------------------


class AddRequest(BaseModel):
    company: str = Field(..., min_length=1, max_length=120, examples=["Acme Corp"])
    role: str = Field(..., min_length=1, max_length=120, examples=["Backend Engineer"])
    status: str = Field("wishlist", examples=["applied"])
    applied_on: Optional[str] = Field(None, examples=["2026-09-01"])
    location: str = ""
    work_mode: str = "unspecified"
    source: str = "other"
    url: str = ""
    currency: str = "USD"
    salary_min: Optional[str] = None
    salary_max: Optional[str] = None
    priority: Optional[int] = None
    deadline_on: Optional[str] = None
    contact: str = ""
    tags: List[str] = Field(default_factory=list)
    today: Optional[str] = None


class MoveRequest(BaseModel):
    status: str
    note: str = ""
    force: bool = False


class ApplyRequest(BaseModel):
    on: Optional[str] = None
    note: str = ""


class StageRequest(BaseModel):
    stage: str
    on: Optional[str] = None
    note: str = ""


class NoteRequest(BaseModel):
    text: str
    on: Optional[str] = None


class FollowupRequest(BaseModel):
    note: str = ""
    channel: str = ""


class NextActionRequest(BaseModel):
    action: str
    on: Optional[str] = None


class InterviewRequest(BaseModel):
    on: Optional[str] = None
    kind: str = "other"
    note: str = ""
    done: bool = False


class DemoRequest(BaseModel):
    today: Optional[str] = None
    force: bool = True


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------


@app.get("/api/health")
def health() -> Dict[str, Any]:
    engine = get_engine()
    return {
        "ok": True,
        "agent": "JOBFLOW",
        "version": __version__,
        "tagline": TAGLINE,
        "applications": len(engine.vault),
        "vault": str(engine.path) if engine.path else None,
    }


@app.get("/api/vocabulary")
def vocabulary() -> Dict[str, Any]:
    """Statuses, stages, sources, rules and thresholds — no hardcoding needed."""

    return JobFlowEngine.vocabulary()


@app.get("/api/routes")
def routes() -> Dict[str, Any]:
    """A discoverable endpoint index (handy for a live demo)."""

    return {
        "endpoints": [
            {"method": "GET", "path": "/api/health", "what": "liveness + vault size"},
            {"method": "GET", "path": "/api/vocabulary", "what": "statuses, stages, rules"},
            {"method": "GET", "path": "/api/applications", "what": "list (filterable)"},
            {"method": "POST", "path": "/api/applications", "what": "add an application"},
            {"method": "GET", "path": "/api/applications/{id}", "what": "one application"},
            {"method": "POST", "path": "/api/applications/{id}/move", "what": "change status"},
            {"method": "POST", "path": "/api/applications/{id}/apply", "what": "log submission"},
            {"method": "POST", "path": "/api/applications/{id}/stage", "what": "backfill a stage"},
            {"method": "POST", "path": "/api/applications/{id}/note", "what": "add a note"},
            {"method": "POST", "path": "/api/applications/{id}/followup", "what": "log a chase"},
            {"method": "POST", "path": "/api/applications/{id}/next", "what": "set next action"},
            {"method": "POST", "path": "/api/applications/{id}/interview", "what": "schedule/complete"},
            {"method": "DELETE", "path": "/api/applications/{id}", "what": "delete"},
            {"method": "GET", "path": "/api/funnel", "what": "cohort-aware funnel"},
            {"method": "GET", "path": "/api/plan", "what": "ranked to-do list with reasons"},
            {"method": "GET", "path": "/api/why/{rule}", "what": "why a rule fired"},
            {"method": "GET", "path": "/api/upcoming", "what": "interviews in the next N days"},
            {"method": "GET", "path": "/api/summary", "what": "the full analytics bundle"},
            {"method": "GET", "path": "/api/status", "what": "text digest"},
            {"method": "GET", "path": "/api/report", "what": "text report"},
            {"method": "GET", "path": "/api/report.md", "what": "Markdown report"},
            {"method": "GET", "path": "/api/export", "what": "machine-readable bundle"},
            {"method": "POST", "path": "/api/demo", "what": "seed the 90-day demo vault"},
            {"method": "POST", "path": "/api/reset", "what": "empty the vault"},
        ]
    }


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def _summary_row(app: Any, when: Any) -> Dict[str, Any]:
    """The derived block shared by the list and detail endpoints.

    Keeping one definition means a client reading ``/api/applications`` and a
    client reading ``/api/applications/{id}`` cannot disagree about the same
    application's derived state.
    """

    return {
        "id": app.id,
        "label": app.label,
        "company": app.company,
        "role": app.role,
        "status": app.status,
        "location": app.location,
        "work_mode": app.work_mode,
        "source": app.source,
        "priority": app.priority,
        "salary_min": app.salary_min,
        "salary_max": app.salary_max,
        "currency": app.currency,
        "applied_on": app.applied_on,
        "furthest_stage": app.furthest_stage(),
        "age_days": app.age_days(when),
        "days_in_stage": app.days_in_stage(when),
        "days_since_activity": app.days_since_activity(when),
        "followups_sent": app.followup_count(),
        "next_action": app.next_action,
        "next_action_on": app.next_action_on,
        "tags": list(app.tags),
    }


@app.get("/api/applications")
def list_applications(
    status: Optional[List[str]] = Query(None),
    active: bool = False,
    tag: Optional[str] = None,
    company: Optional[str] = None,
    today: Optional[str] = None,
) -> Dict[str, Any]:
    engine = get_engine()
    when = engine.resolve_today(today)
    apps = engine.list(statuses=status, active_only=active, tag=tag, company=company)
    return {
        "count": len(apps),
        "applications": [_summary_row(a, when) for a in apps],
    }


@app.get("/api/applications/{app_id}")
def get_application(app_id: str, today: Optional[str] = None) -> Dict[str, Any]:
    """One application: the stored record *plus* the same derived block the
    list endpoint returns, so a detail view needs no extra computation."""

    engine = get_engine()
    try:
        app = engine.get(app_id)
    except Exception as exc:  # noqa: BLE001 - mapped to a status code
        raise _fail(exc)
    when = engine.resolve_today(today)
    payload = app.to_dict()
    payload.update(_summary_row(app, when))
    payload["derived"] = {
        "furthest_stage": app.furthest_stage(),
        "reached_index": app.reached_index(),
        "has_response": app.has_response(),
        "is_active": app.is_active,
        "is_closed": app.is_closed,
        "last_activity_on": app.last_activity_on(),
    }
    return payload


@app.get("/api/funnel")
def get_funnel(today: Optional[str] = None) -> Dict[str, Any]:
    return get_engine().funnel(today=today)


@app.get("/api/plan")
def get_plan(today: Optional[str] = None, limit: int = 10) -> Dict[str, Any]:
    return get_engine().plan(today=today, limit=limit)


@app.get("/api/why/{rule}")
def get_why(rule: str, app_id: Optional[str] = None, today: Optional[str] = None) -> Dict[str, Any]:
    return get_engine().explain(rule, app_id=app_id, today=today)


@app.get("/api/upcoming")
def get_upcoming(today: Optional[str] = None, days: int = 14) -> Dict[str, Any]:
    rows = get_engine().upcoming(today=today, days=days)
    return {"count": len(rows), "interviews": rows}


@app.get("/api/summary")
def get_summary(today: Optional[str] = None) -> Dict[str, Any]:
    return get_engine().summary(today=today)


@app.get("/api/status", response_class=PlainTextResponse)
def get_status(today: Optional[str] = None) -> str:
    return get_engine().render_status(today=today)


@app.get("/api/report", response_class=PlainTextResponse)
def get_report(today: Optional[str] = None) -> str:
    return get_engine().render_report(today=today)


@app.get("/api/report.md", response_class=PlainTextResponse)
def get_markdown(today: Optional[str] = None) -> str:
    return get_engine().render_markdown(today=today)


@app.get("/api/export")
def get_export(today: Optional[str] = None) -> Dict[str, Any]:
    return get_engine().export(today=today)


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


@app.post("/api/applications", status_code=201)
def add_application(payload: AddRequest) -> Dict[str, Any]:
    engine = get_engine()
    try:
        data = payload.model_dump(exclude_none=False)
        today = data.pop("today", None)
        app = engine.add(data.pop("company"), data.pop("role"), today=today, **data)
        engine.save()
        return {"ok": True, "application": app.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/applications/{app_id}/move")
def move_application(app_id: str, payload: MoveRequest, today: Optional[str] = None) -> Dict[str, Any]:
    engine = get_engine()
    try:
        app = engine.move(app_id, payload.status, today=today, note=payload.note, force=payload.force)
        engine.save()
        return {"ok": True, "application": app.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/applications/{app_id}/apply")
def apply_application(app_id: str, payload: ApplyRequest, today: Optional[str] = None) -> Dict[str, Any]:
    engine = get_engine()
    try:
        app = engine.log_apply(app_id, today=today, on=payload.on, note=payload.note)
        engine.save()
        return {"ok": True, "application": app.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/applications/{app_id}/stage")
def stage_application(app_id: str, payload: StageRequest, today: Optional[str] = None) -> Dict[str, Any]:
    engine = get_engine()
    try:
        app = engine.log_stage(app_id, payload.stage, today=today, on=payload.on, note=payload.note)
        engine.save()
        return {"ok": True, "application": app.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/applications/{app_id}/note")
def note_application(app_id: str, payload: NoteRequest, today: Optional[str] = None) -> Dict[str, Any]:
    engine = get_engine()
    try:
        app = engine.note(app_id, payload.text, today=today, on=payload.on)
        engine.save()
        return {"ok": True, "application": app.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/applications/{app_id}/followup")
def followup_application(app_id: str, payload: FollowupRequest, today: Optional[str] = None) -> Dict[str, Any]:
    engine = get_engine()
    try:
        app = engine.followup(app_id, today=today, note=payload.note, channel=payload.channel)
        engine.save()
        return {"ok": True, "application": app.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/applications/{app_id}/next")
def next_action(app_id: str, payload: NextActionRequest) -> Dict[str, Any]:
    engine = get_engine()
    try:
        app = engine.set_next_action(app_id, payload.action, on=payload.on)
        engine.save()
        return {"ok": True, "application": app.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/applications/{app_id}/interview")
def interview(app_id: str, payload: InterviewRequest, today: Optional[str] = None) -> Dict[str, Any]:
    engine = get_engine()
    try:
        if payload.done:
            app = engine.complete_interview(app_id, today=today, on=payload.on, note=payload.note)
            engine.save()
            return {"ok": True, "application": app.to_dict()}
        stamp = payload.on or engine.resolve_today(today).isoformat()
        item = engine.schedule_interview(app_id, stamp, kind=payload.kind, note=payload.note)
        engine.save()
        return {"ok": True, "interview": item.to_dict()}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.delete("/api/applications/{app_id}")
def delete_application(app_id: str) -> Dict[str, Any]:
    engine = get_engine()
    try:
        app = engine.drop(app_id)
        engine.save()
        return {"ok": True, "deleted": app.id}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/demo")
def seed_demo(payload: Optional[DemoRequest] = None) -> Dict[str, Any]:
    engine = get_engine()
    request = payload or DemoRequest()
    try:
        info = engine.seed_demo(today=request.today, force=request.force)
        engine.save()
        return {"ok": True, "seeded": info}
    except Exception as exc:  # noqa: BLE001
        raise _fail(exc)


@app.post("/api/reset")
def reset_vault() -> Dict[str, Any]:
    engine = get_engine()
    engine.vault.applications = []
    engine.save()
    return {"ok": True, "applications": 0}


@app.get("/", response_class=PlainTextResponse)
def root() -> str:
    return (
        f"{TAGLINE}\n\n"
        f"See /docs for the interactive API, or /api/routes for the endpoint index.\n"
        f"Static demo: web-live/index.html\n"
    )
