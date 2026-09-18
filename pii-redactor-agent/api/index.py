"""FastAPI layer.

Thin by design: it validates input, calls the same `Scanner` the CLI uses, and
returns JSON. No detection or transformation logic lives here, so the API cannot
drift from the CLI — a property asserted by `tests/test_api.py`, which runs the
same document through both and compares results.

Run locally::

    uvicorn api.index:app --reload --port 8000

Endpoints
---------
``GET  /health``                 liveness and version
``GET  /entities``               the catalogue and what this policy will do
``POST /scan``                   detect only
``POST /redact``                 redact and verify
``POST /verify``                 assert that a document is clean
``POST /detokenize``             restore tokenized output from a vault
``GET  /reference``              entity + action reference tables
``GET  /demo``                   the sample bundle and policies
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional

# Allow `uvicorn api.index:app` from the project root without installing.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from veil import __version__, demo  # noqa: E402
from veil.errors import PolicyError, VeilError, VaultError  # noqa: E402
from veil.models import ACTIONS, ALL_ENTITIES  # noqa: E402
from veil.policy import Policy, from_dict  # noqa: E402
from veil.scanner import Scanner  # noqa: E402
from veil.vault import Vault  # noqa: E402

app = FastAPI(
    title="VEIL — PII Redaction API",
    version=__version__,
    description=(
        "Deterministic PII and secret redaction. Detects, transforms, and then "
        "re-scans its own output to verify nothing survived. Fully offline: no "
        "network calls, no model, no API key."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class PolicySpec(BaseModel):
    """A policy supplied inline, mirroring the YAML keys one-for-one."""

    name: Optional[str] = None
    entities: Optional[List[str]] = None
    actions: Optional[Dict[str, str]] = None
    keep_first: Optional[int] = None
    keep_last: Optional[int] = None
    mask_char: Optional[str] = None
    hash_salt: Optional[str] = None
    token_prefix: Optional[str] = None
    allowlist: Optional[List[str]] = None
    denylist: Optional[List[str]] = None
    names: Optional[List[str]] = None
    case_sensitive_denylist: Optional[bool] = None


class DocumentRequest(BaseModel):
    text: str = Field(..., description="The document to process.")
    policy: Optional[PolicySpec] = Field(
        None, description="Inline policy. Omit to use the built-in defaults."
    )
    include_values: bool = Field(
        True,
        description=(
            "Whether findings carry their raw values. Set false when the response "
            "will be logged or shared."
        ),
    )


class RedactRequest(DocumentRequest):
    verify: bool = Field(True, description="Re-scan the output and report residuals.")


class DetokenizeRequest(BaseModel):
    text: str
    vault: Dict[str, str] = Field(
        ..., description="Token -> original value, as produced by /redact."
    )


class FindingOut(BaseModel):
    entity: str
    value: str
    action: str
    replacement: str
    count: int
    detector: str
    confidence: float
    note: str
    preserved: bool
    risk_weight: int


class RedactResponse(BaseModel):
    policy: str
    redacted: str
    findings: List[FindingOut]
    applied_count: int
    risk: dict
    verification: Optional[dict] = None
    token_map: Dict[str, str] = Field(default_factory=dict)


class ScanResponse(BaseModel):
    policy: str
    findings: List[FindingOut]
    applied_count: int
    risk: dict
    note: str = (
        "Scan-only: nothing was changed. This response contains no redacted "
        "document — use POST /redact for that."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_policy(spec: Optional[PolicySpec]) -> Policy:
    if spec is None:
        return Policy()
    payload = {k: v for k, v in spec.model_dump().items() if v is not None}

    # Reject unknown entity names and actions explicitly. Silently ignoring them
    # would be the worst possible failure mode for this tool: a caller who typos
    # `EMAIL_ADDRESS` would get a 200 and a "clean" report while their addresses
    # sailed straight through. A hard 422 is the only safe answer.
    #
    # The `entities` list accepts the same selectors as the YAML: the literal
    # `ALL`, and `!ENTITY` to subtract from what has been selected so far.
    unknown_entities = []
    for raw in payload.get("entities") or []:
        key = str(raw).strip().upper()
        if key == "ALL":
            continue
        if key.startswith("!"):
            key = key[1:]
        if key not in ALL_ENTITIES:
            unknown_entities.append(str(raw))
    if unknown_entities:
        raise HTTPException(
            status_code=422,
            detail=(
                "unknown entity name(s): " + ", ".join(sorted(set(unknown_entities)))
                + ". Valid names are listed by GET /entities."
            ),
        )
    unknown_actions = sorted(
        {
            value for value in (payload.get("actions") or {}).values()
            if value not in ACTIONS
        }
    )
    if unknown_actions:
        raise HTTPException(
            status_code=422,
            detail=(
                "unknown action(s): " + ", ".join(unknown_actions)
                + ". Valid actions are: " + ", ".join(ACTIONS) + "."
            ),
        )
    unknown_keys = sorted(
        set(payload.get("actions") or {}) - set(ALL_ENTITIES)
    )
    if unknown_keys:
        raise HTTPException(
            status_code=422,
            detail=(
                "actions given for unknown entity name(s): "
                + ", ".join(unknown_keys)
            ),
        )

    try:
        return from_dict(payload, source="request")
    except PolicyError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None


def _findings_payload(findings, include_values: bool) -> List[dict]:
    out: List[dict] = []
    for finding in findings:
        data = finding.to_dict() if include_values else finding.to_public_dict()
        out.append({
            "entity": data["entity"],
            "value": data["value"],
            "action": data["action"],
            "replacement": data["replacement"],
            "count": data["count"],
            "detector": data["detector"],
            "confidence": data["confidence"],
            "note": data["note"],
            "preserved": data["preserved"],
            "risk_weight": data["risk_weight"],
        })
    return out


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "veil",
        "version": __version__,
        "offline": True,
        "entities": len(ALL_ENTITIES),
    }


@app.get("/entities")
def entities() -> dict:
    """The entity catalogue plus the action the default policy applies."""
    scanner = Scanner()
    return {
        "policy": scanner.policy.name,
        "count": len(ALL_ENTITIES),
        "entities": scanner.entity_report(),
    }


@app.get("/reference")
def reference() -> dict:
    from veil.actions import describe_actions
    from veil.detectors import DETECTOR_ORDER
    from veil.models import catalogue

    return {
        "entities": catalogue(),
        "detector_order": list(DETECTOR_ORDER),
        "actions": describe_actions(),
    }


@app.get("/demo")
def demo_bundle() -> dict:
    """The synthetic sample bundle, so a caller can try the API with no input."""
    return {
        "documents": [
            {"name": name, "text": body, **meta}
            for name, body, meta in (
                (n, demo.DOCUMENTS[n], row)
                for n, row in ((r["name"], r) for r in demo.document_summary())
            )
        ],
        "policies": demo.policy_summary(),
        "default_policy": demo.DEFAULT_POLICY,
    }


@app.get("/demo/{name}")
def demo_document(name: str) -> dict:
    if name not in demo.DOCUMENTS:
        raise HTTPException(
            status_code=404,
            detail=f"no sample named {name!r}. Available: {', '.join(demo.DOCUMENTS)}",
        )
    return {"name": name, "text": demo.DOCUMENTS[name]}


@app.post("/scan", response_model=ScanResponse)
def scan(request: DocumentRequest) -> dict:
    """Detect and report. The document is never returned."""
    scanner = Scanner(_build_policy(request.policy))
    try:
        result = scanner.scan(request.text)
    except VeilError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    return {
        "policy": result.policy_name,
        "findings": _findings_payload(result.findings, request.include_values),
        "applied_count": len(result.applied),
        "risk": result.risk.to_dict() if result.risk else {},
    }


@app.post("/redact", response_model=RedactResponse)
def redact(request: RedactRequest) -> dict:
    scanner = Scanner(_build_policy(request.policy))
    try:
        result = scanner.redact(request.text, verify_output=request.verify)
    except VeilError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None
    return {
        "policy": result.policy_name,
        "redacted": result.text,
        "findings": _findings_payload(result.findings, request.include_values),
        "applied_count": len(result.applied),
        "risk": result.risk.to_dict() if result.risk else {},
        "verification": result.verification.to_dict() if result.verification else None,
        "token_map": dict(result.token_map),
    }


@app.post("/verify")
def verify(request: RedactRequest) -> dict:
    """Redact, then assert the output is clean. 409 when a residual survives."""
    scanner = Scanner(_build_policy(request.policy))
    try:
        result = scanner.redact(request.text, verify_output=True)
    except VeilError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None

    verification = result.verification
    payload = {
        "policy": result.policy_name,
        "verification": verification.to_dict() if verification else None,
        "risk_level": result.risk.level if result.risk else None,
        "applied_count": len(result.applied),
    }
    if verification is not None and not verification.clean:
        raise HTTPException(status_code=409, detail=payload)
    return payload


@app.post("/detokenize")
def detokenize(request: DetokenizeRequest) -> dict:
    if not request.vault:
        raise HTTPException(status_code=422, detail="vault must not be empty")
    try:
        vault = Vault.from_mapping(request.vault)
    except VaultError as exc:
        raise HTTPException(status_code=422, detail=exc.message) from None

    scanner = Scanner()
    restored = scanner.detokenize(request.text, vault)
    return {
        "restored": restored,
        "restored_count": len(vault),
        "by_entity": vault.summary(),
    }


def handler(request):  # pragma: no cover - Vercel entry point
    """AWS-Lambda-style shim, used when deployed as a Vercel Python function."""
    from mangum import Mangum

    return Mangum(app)(request, None)


if __name__ == "__main__":  # pragma: no cover
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
