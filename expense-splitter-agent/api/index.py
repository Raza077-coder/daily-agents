"""SplitKit REST API — a thin FastAPI layer over the same engine the CLI uses.

Every endpoint delegates to :class:`splitkit.SplitKit`, so a number returned
here is the number `splitkit report` prints. Errors map cleanly onto status
codes:

* ``SplitKitError`` is a ``ValueError`` subclass → **400** (the caller's data is
  wrong), including unknown members (404 when the member/expense itself is the
  resource being addressed) and malformed money.
* Anything else → **500**, because that would be our bug.

The ASGI application is exported as ``app`` so Vercel's Python runtime can serve
it directly from ``api/index.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow `uvicorn api.index:app` from the project root as well as the Vercel
# runtime, where the repo root may not be on sys.path.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import Body, FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from splitkit import __version__  # noqa: E402
from splitkit.config import load_config  # noqa: E402
from splitkit.demo import build_demo_group  # noqa: E402
from splitkit.engine import SplitKit, currency_reference, describe_modes  # noqa: E402
from splitkit.errors import (  # noqa: E402
    SplitKitError,
    UnknownExpenseError,
    UnknownMemberError,
)
from splitkit.money import CURRENCY_EXPONENTS  # noqa: E402
from splitkit.splits import MODES  # noqa: E402

app = FastAPI(
    title="SplitKit API",
    version=__version__,
    description=(
        "Shared expense splitting and minimum-transfer settle-up. "
        "All money is handled as exact integer minor units: a split that does "
        "not sum to its expense is rejected with a 400 rather than rounded."
    ),
)

# The web demo is a static page, but allowing CORS makes the API usable from
# any front end the owner wants to build on top of it.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

#: Single in-process group. Serverless functions are ephemeral, so this makes
#: the API a demoable session rather than durable storage — documented in the
#: README, and the reason `GET /group` exists for export.
_STATE: Dict[str, Optional[SplitKit]] = {"kit": None}


def _kit() -> SplitKit:
    kit = _STATE["kit"]
    if kit is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "No group loaded. POST /demo to load the seeded example group, or "
                "POST /group to create one."
            ),
        )
    return kit


# --------------------------------------------------------------------------- #
# request models
# --------------------------------------------------------------------------- #


class MemberIn(BaseModel):
    name: str = Field(..., min_length=1, description="Display name")
    id: Optional[str] = Field(None, description="Optional explicit member id")


class GroupIn(BaseModel):
    name: str = Field(..., min_length=1)
    members: List[str] = Field(..., min_length=1)
    currency: str = Field("USD", min_length=3, max_length=3)


class ExpenseIn(BaseModel):
    description: str = Field(..., min_length=1)
    amount: str = Field(..., description='Amount as a string, e.g. "92.40"')
    paid_by: str = Field(..., description="Member id who fronted the money")
    split: Optional[Dict[str, Any]] = Field(
        None, description='Split spec, e.g. {"mode":"shares","shares":{"ali":2,"sara":1}}'
    )
    date: Optional[str] = None
    category: str = "other"
    note: str = ""


class SettlementIn(BaseModel):
    from_member: str
    to_member: str
    amount: str = Field(..., description='Amount as a string, e.g. "30.00"')
    date: Optional[str] = None
    note: str = ""


class AskIn(BaseModel):
    question: str = Field("who owes what", description="Short natural question")


# --------------------------------------------------------------------------- #
# error mapping
# --------------------------------------------------------------------------- #


def _error_response(status: int, exc: Exception):
    """Uniform error body: a human message plus a machine-readable code."""
    from fastapi.responses import JSONResponse

    return JSONResponse(
        status_code=status,
        content={"detail": str(exc), "code": getattr(exc, "code", "error")},
    )


# Most-specific class wins, so a missing member/expense is a 404 even though
# both also derive from SplitKitError. Handlers (not try/except inside each
# endpoint) own this mapping, so nothing can accidentally outrank them.
@app.exception_handler(UnknownMemberError)
async def _unknown_member(_request, exc: UnknownMemberError):
    return _error_response(404, exc)


@app.exception_handler(UnknownExpenseError)
async def _unknown_expense(_request, exc: UnknownExpenseError):
    return _error_response(404, exc)


@app.exception_handler(SplitKitError)
async def _splitkit_error(_request, exc: SplitKitError):
    """Any other engine rejection is a 400 — the caller's data is wrong."""
    return _error_response(400, exc)


# --------------------------------------------------------------------------- #
# endpoints
# --------------------------------------------------------------------------- #


@app.get("/", tags=["meta"])
def root() -> Dict[str, Any]:
    """Service banner with the endpoints that matter."""
    return {
        "service": "SplitKit",
        "version": __version__,
        "tagline": "Shared costs, settled exactly.",
        "endpoints": [
            "GET  /health", "GET  /modes", "GET  /currencies", "GET  /config",
            "POST /demo", "POST /group", "GET  /group",
            "GET  /members", "POST /members",
            "GET  /expenses", "POST /expense", "DELETE /expense/{id}",
            "GET  /balances", "GET  /settle", "GET  /report",
            "POST /settlement", "GET  /settlements",
            "POST /ask", "GET  /verify",
        ],
    }


@app.get("/health", tags=["meta"])
def health() -> Dict[str, Any]:
    """Liveness probe; also reports whether a group is loaded."""
    kit = _STATE["kit"]
    return {
        "status": "ok",
        "version": __version__,
        "group_loaded": kit is not None,
        "group": kit.group.id if kit else None,
        "expenses": len(kit.group.expenses) if kit else 0,
    }


@app.get("/modes", tags=["reference"])
def modes() -> Dict[str, str]:
    """The six split modes and what each one means."""
    return describe_modes()


@app.get("/currencies", tags=["reference"])
def currencies() -> Dict[str, Any]:
    """Currencies SplitKit knows, with their decimal places."""
    return currency_reference()


@app.get("/config", tags=["reference"])
def config() -> Dict[str, Any]:
    """The resolved runtime configuration."""
    cfg = load_config()
    return {"settings": cfg.to_dict(), "sources": cfg.source_files}


@app.post("/demo", tags=["group"])
def load_demo(currency: str = Query("USD")) -> Dict[str, Any]:
    """Load the seeded example group — the fastest way to try the API."""
    if currency.upper() not in CURRENCY_EXPONENTS:
        raise HTTPException(status_code=400, detail=f"unknown currency {currency!r}")
    kit = SplitKit(group=build_demo_group(currency.upper()))
    _STATE["kit"] = kit
    return {"loaded": True, "group": kit.group.id, "report": kit.report()}


@app.post("/group", tags=["group"], status_code=201)
def create_group(payload: GroupIn) -> Dict[str, Any]:
    """Create a group, replacing any currently loaded one."""
    kit = SplitKit.create(payload.name, payload.members, currency=payload.currency, auto_save=False)
    _STATE["kit"] = kit
    return kit.to_dict()


@app.get("/group", tags=["group"])
def get_group() -> Dict[str, Any]:
    """Export the whole group as JSON (members, expenses, settlements)."""
    return _kit().to_dict()


@app.get("/members", tags=["group"])
def list_members() -> Dict[str, Any]:
    """Members with their paid / share / net position."""
    kit = _kit()
    return {"members": kit.balance_table()}


@app.post("/members", tags=["group"], status_code=201)
def add_member(payload: MemberIn) -> Dict[str, Any]:
    """Add a participant."""
    return _kit().add_member(payload.name, member_id=payload.id)


@app.get("/expenses", tags=["expenses"])
def list_expenses() -> Dict[str, Any]:
    """Every expense with its resolved per-member shares."""
    return {"expenses": _kit().expenses()}


@app.post("/expense", tags=["expenses"], status_code=201)
def add_expense(payload: ExpenseIn) -> Dict[str, Any]:
    """Record an expense. The split is validated before anything is stored."""
    return _kit().add_expense(
        payload.description,
        payload.amount,
        payload.paid_by,
        payload.split,
        date=payload.date,
        category=payload.category,
        note=payload.note,
    )


@app.delete("/expense/{expense_id}", tags=["expenses"])
def delete_expense(expense_id: str) -> Dict[str, Any]:
    """Delete an expense by id."""
    return _kit().remove_expense(expense_id)


@app.get("/balances", tags=["analysis"])
def balances() -> Dict[str, Any]:
    """Net position per member, plus the zero-sum check."""
    kit = _kit()
    net = kit.balances()
    return {
        "rows": kit.balance_table(),
        "sum_minor": sum(net.values()),
        "balanced": sum(net.values()) == 0,
        "summary": kit.summary(),
    }


@app.get("/settle", tags=["analysis"])
def settle(
    strategy: str = Query("optimal", pattern="^(greedy|optimal|compare)$"),
) -> Dict[str, Any]:
    """The settle-up plan, with a proof that it clears every balance."""
    kit = _kit()
    payload = kit.settle(strategy)
    payload["verified"] = kit.plan_is_valid(strategy)
    return payload


@app.get("/report", tags=["analysis"])
def report(
    fmt: str = Query("json", alias="format", pattern="^(json|text|markdown)$"),
    include_expenses: bool = Query(True),
) -> Any:
    """The full report as JSON, plain text, or Markdown."""
    from fastapi.responses import PlainTextResponse

    kit = _kit()
    if fmt == "text":
        return PlainTextResponse(kit.render_text(include_expenses=include_expenses))
    if fmt == "markdown":
        return PlainTextResponse(kit.render_markdown(include_expenses=include_expenses))
    return kit.report(include_expenses=include_expenses)


@app.post("/settlement", tags=["settlements"], status_code=201)
def add_settlement(payload: SettlementIn) -> Dict[str, Any]:
    """Record an actual repayment and return the refreshed plan."""
    kit = _kit()
    settlement = kit.record_settlement(
        payload.from_member, payload.to_member, payload.amount,
        date=payload.date, note=payload.note,
    )
    plan = kit.settle()
    return {"settlement": settlement, "settle_plan": plan, "settled": not plan["transfers"]}


@app.get("/settlements", tags=["settlements"])
def list_settlements() -> Dict[str, Any]:
    """Repayments recorded so far."""
    return {"settlements": _kit().settlements()}


@app.post("/ask", tags=["agent"])
def ask(payload: AskIn = Body(default=AskIn())) -> Dict[str, Any]:
    """Answer a short question about the group ("who owes what", "settle up").

    Deliberately keyword-routed rather than model-backed: the request space is
    small and known, and a deterministic router means the same question always
    yields the same answer offline with no key.
    """
    return _kit().answer(payload.question)


@app.get("/verify", tags=["analysis"])
def verify() -> Dict[str, Any]:
    """Assert the ledger balances and the plan provably clears it."""
    kit = _kit()
    net = kit.balances()
    return {
        "balanced": sum(net.values()) == 0,
        "sum_of_balances_minor": sum(net.values()),
        "settle_plan_valid": kit.plan_is_valid(),
        "transfer_count": kit.settle()["transfer_count"],
    }


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
