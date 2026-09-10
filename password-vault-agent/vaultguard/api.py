"""VAULTGUARD REST API (FastAPI).

All endpoints are stateless: the master password is supplied per request and the
vault is opened, mutated and re-encrypted inside that single request. Nothing is
cached between calls, so a restart never leaks an unlocked vault.

Mount locally::

    uvicorn vaultguard.api:app --reload

Endpoints
    GET  /health              liveness + cipher availability
    GET  /persona             the VAULTGUARD agent persona/config
    GET  /docs                auto-generated OpenAPI UI
    POST /vault/create        create a vault
    GET  /vault/info          metadata (works while locked)
    POST /vault/unlock        verify a master password
    POST /vault/audit         run the security audit
    POST /vault/stats         aggregate statistics
    POST /vault/entries       list / search entries
    POST /vault/entries/add   add an entry
    POST /vault/entries/get   fetch one entry (optionally revealing the secret)
    POST /vault/entries/update
    POST /vault/entries/delete
    POST /generate            generate a password / passphrase / PIN
    POST /check               score an arbitrary password
    POST /demo                build a throwaway demo vault
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional

try:  # pragma: no cover - import guard for environments without FastAPI
    from fastapi import APIRouter, FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from pydantic import BaseModel, Field

    FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover
    FASTAPI_AVAILABLE = False
    FastAPI = None  # type: ignore[assignment]
    APIRouter = None  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment,misc]
    Field = None  # type: ignore[assignment]
    HTTPException = None  # type: ignore[assignment]
    CORSMiddleware = None  # type: ignore[assignment]

from .audit import VaultAuditor
from .crypto import AES_GCM, HMAC_CTR_ETM, HAS_AES_GCM, default_cipher
from .engine import SearchQuery, VaultAuthError, VaultEngine, VaultError
from .generator import generate_passphrase, generate_password, generate_pin
from .models import CATEGORIES, VaultEntry
from .strength import estimate_strength
from .storage import DEFAULT_VAULT_PATH, VAULT_FORMAT, VaultFormatError


# --------------------------------------------------------------------------- #
# request models (only defined when FastAPI is importable)
# --------------------------------------------------------------------------- #
if FASTAPI_AVAILABLE:

    class CreateRequest(BaseModel):
        master_password: str = Field(..., min_length=8)
        vault_path: Optional[str] = None
        force: bool = False
        seed_demo: bool = False

    class AuthRequest(BaseModel):
        master_password: str
        vault_path: Optional[str] = None

    class AddRequest(BaseModel):
        master_password: str
        title: str
        username: str = ""
        password: str = ""
        url: str = ""
        category: str = "login"
        notes: str = ""
        tags: List[str] = Field(default_factory=list)
        totp: str = ""
        favorite: bool = False
        generate: bool = False
        length: int = 20
        vault_path: Optional[str] = None

    class GetRequest(BaseModel):
        master_password: str
        entry_id: str
        reveal: bool = False
        vault_path: Optional[str] = None

    class UpdateRequest(BaseModel):
        master_password: str
        entry_id: str
        title: Optional[str] = None
        username: Optional[str] = None
        password: Optional[str] = None
        url: Optional[str] = None
        category: Optional[str] = None
        notes: Optional[str] = None
        tags: Optional[List[str]] = None
        totp: Optional[str] = None
        favorite: Optional[bool] = None
        vault_path: Optional[str] = None

    class DeleteRequest(BaseModel):
        master_password: str
        entry_id: str
        vault_path: Optional[str] = None

    class ListRequest(BaseModel):
        master_password: str
        text: str = ""
        category: str = ""
        tag: str = ""
        favorites_only: bool = False
        weak_only: bool = False
        stale_only: bool = False
        sort: str = "title"
        descending: bool = False
        limit: int = 0
        vault_path: Optional[str] = None

    class GenerateRequest(BaseModel):
        kind: str = "password"
        length: int = 20
        words: int = 4

    class CheckRequest(BaseModel):
        password: str


# --------------------------------------------------------------------------- #
# engine plumbing
# --------------------------------------------------------------------------- #
DEMO_VAULT_DIR = os.path.join(tempfile.gettempdir(), "vaultguard-demo")


def _vault_path(override: Optional[str] = None) -> str:
    """Resolve the vault path for a request."""
    explicit = override or os.environ.get("VAULTGUARD_PATH")
    if explicit:
        return explicit
    os.makedirs(DEMO_VAULT_DIR, exist_ok=True)
    return os.path.join(DEMO_VAULT_DIR, "vault.json")


def _open(path: Optional[str], master: str) -> VaultEngine:
    engine = VaultEngine(_vault_path(path))
    if not engine.exists():
        raise HTTPException(status_code=404, detail="vault not found — create it first")
    try:
        engine.unlock(master)
    except VaultAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except VaultFormatError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return engine


def _persona() -> Dict[str, Any]:
    """Load the VAULTGUARD persona/config document."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for candidate in ("vaultguard_persona.json", os.path.join("config", "vaultguard_persona.json")):
        path = os.path.join(here, candidate)
        if os.path.isfile(path):
            import json

            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    return {}


# --------------------------------------------------------------------------- #
# app factory
# --------------------------------------------------------------------------- #
def create_app() -> "FastAPI":
    """Build the FastAPI application (also used by the Vercel entrypoint)."""
    if not FASTAPI_AVAILABLE:  # pragma: no cover
        raise RuntimeError("FastAPI is not installed — run: pip install -r requirements.txt")

    app = FastAPI(
        title="VAULTGUARD API",
        version="1.0.0",
        description=(
            "Offline, encrypted password vault agent. Deterministic, zero network calls. "
            "The master password is supplied per request and never persisted."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ------------------------------------------------------------------ #
    router = APIRouter()

    @app.get("/", include_in_schema=False)
    def index() -> Dict[str, str]:
        return {
            "agent": "VAULTGUARD",
            "version": "1.0.0",
            "docs": "/docs",
            "health": "/health",
            "persona": "/persona",
            "live_demo": "https://raza077-coder.github.io/daily-agents/password-vault-agent/web-live/",
        }

    @app.get("/health")
    def health() -> Dict[str, Any]:
        return {
            "status": "ok",
            "agent": "VAULTGUARD",
            "version": "1.0.0",
            "format": VAULT_FORMAT,
            "preferred_cipher": default_cipher(),
            "aes_gcm_available": HAS_AES_GCM,
            "ciphers": [AES_GCM, HMAC_CTR_ETM],
            "network_calls": "none",
        }

    @app.get("/persona")
    def persona() -> Dict[str, Any]:
        return _persona()

    @app.get("/categories")
    def categories() -> Dict[str, Any]:
        return {"categories": list(CATEGORIES)}

    # -- vault lifecycle ------------------------------------------------ #
    @app.post("/vault/create")
    def vault_create(req: "CreateRequest") -> Dict[str, Any]:
        engine = VaultEngine(_vault_path(req.vault_path))
        try:
            result = engine.create(req.master_password, force=req.force)
        except VaultError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if req.seed_demo:
            from .cli import seed_demo

            seed_demo(engine)
            engine.save()
            result["seeded"] = True
        return result

    @app.get("/vault/info")
    def vault_info(vault_path: Optional[str] = None) -> Dict[str, Any]:
        return VaultEngine(_vault_path(vault_path)).info()

    @app.post("/vault/unlock")
    def vault_unlock(req: "AuthRequest") -> Dict[str, Any]:
        engine = VaultEngine(_vault_path(req.vault_path))
        if not engine.exists():
            raise HTTPException(status_code=404, detail="vault not found")
        try:
            return engine.unlock(req.master_password)
        except VaultAuthError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except VaultFormatError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/vault/audit")
    def vault_audit(req: "AuthRequest") -> Dict[str, Any]:
        return _open(req.vault_path, req.master_password).audit().to_dict()

    @app.post("/vault/stats")
    def vault_stats(req: "AuthRequest") -> Dict[str, Any]:
        engine = _open(req.vault_path, req.master_password)
        data = engine.stats()
        data["duplicates"] = engine.duplicates()
        data["health_score"] = engine.health_score()
        return data

    # -- entries -------------------------------------------------------- #
    @app.post("/vault/entries")
    def entries_list(req: "ListRequest") -> Dict[str, Any]:
        engine = _open(req.vault_path, req.master_password)
        query = SearchQuery(
            text=req.text,
            category=req.category,
            tag=req.tag,
            favorites_only=req.favorites_only,
            weak_only=req.weak_only,
            stale_only=req.stale_only,
            sort=req.sort,
            descending=req.descending,
            limit=req.limit,
        )
        found = engine.search(query)
        return {"count": len(found), "entries": found}

    @app.post("/vault/entries/add")
    def entries_add(req: "AddRequest") -> Dict[str, Any]:
        engine = _open(req.vault_path, req.master_password)
        try:
            return engine.add(
                title=req.title,
                username=req.username,
                password=req.password,
                url=req.url,
                category=req.category,
                notes=req.notes,
                tags=req.tags,
                totp=req.totp,
                favorite=req.favorite,
                generate=req.generate,
                length=req.length,
            )
        except (VaultError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/vault/entries/get")
    def entries_get(req: "GetRequest") -> Dict[str, Any]:
        engine = _open(req.vault_path, req.master_password)
        if req.reveal:
            return engine.reveal(req.entry_id)
        try:
            return engine.get(req.entry_id)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/vault/entries/update")
    def entries_update(req: "UpdateRequest") -> Dict[str, Any]:
        engine = _open(req.vault_path, req.master_password)
        changes = {
            key: value
            for key, value in req.model_dump(exclude_none=True).items()
            if key not in ("master_password", "entry_id", "vault_path")
        }
        if not changes:
            raise HTTPException(status_code=400, detail="no fields to update")
        try:
            return engine.update(req.entry_id, **changes)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/vault/entries/delete")
    def entries_delete(req: "DeleteRequest") -> Dict[str, Any]:
        engine = _open(req.vault_path, req.master_password)
        try:
            return engine.delete(req.entry_id)
        except VaultError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- utilities ------------------------------------------------------ #
    @app.post("/generate")
    def generate(req: "GenerateRequest") -> Dict[str, Any]:
        try:
            if req.kind == "passphrase":
                secret = generate_passphrase(req.words, add_number=True)
            elif req.kind == "pin":
                secret = generate_pin(req.length)
            else:
                secret = generate_password(req.length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"kind": req.kind, "secret": secret, "strength": estimate_strength(secret).to_dict()}

    @app.post("/check")
    def check(req: "CheckRequest") -> Dict[str, Any]:
        return estimate_strength(req.password).to_dict()

    @app.post("/demo")
    def demo(vault_path: Optional[str] = None) -> Dict[str, Any]:
        """Create a throwaway vault with sample data and return its full report."""
        path = _vault_path(vault_path)
        engine = VaultEngine(path, iterations=50_000)
        engine.create("demo-master-password", force=True)
        from .cli import seed_demo

        seed_demo(engine)
        engine.save()
        return {
            "vault_path": path,
            "master_password": "demo-master-password",
            "entries": engine.list_all(),
            "stats": engine.stats(),
            "audit": engine.audit().to_dict(),
        }

    app.include_router(router)
    return app


app = create_app() if FASTAPI_AVAILABLE else None  # type: ignore[assignment]


def run() -> None:  # pragma: no cover - convenience entrypoint
    """``python -m vaultguard.api`` — start uvicorn on port 8000."""
    import uvicorn

    uvicorn.run("vaultguard.api:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)


if __name__ == "__main__":  # pragma: no cover
    run()
