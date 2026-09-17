"""Vault persistence for JOBFLOW.

The store owns exactly three responsibilities:

1. Load and validate a vault file from disk.
2. Write it back **atomically** (temp file + ``os.replace``) so an interrupted
   write can never leave a half-written vault behind.
3. Keep the document in a canonical, diff-friendly shape.

It deliberately knows nothing about job-hunt semantics — that all lives in
:mod:`jobtracker.pipeline` and :mod:`jobtracker.analytics`.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import (
    Application,
    JobFlowError,
    NotFoundError,
    ValidationError,
    clean_text,
    iso,
    parse_date,
)

SCHEMA_VERSION = 1
DEFAULT_VAULT_NAME = "jobflow.json"


def default_vault_path() -> Path:
    """Where a vault lives when the caller does not say.

    ``JOBFLOW_VAULT`` wins, then ``./jobflow.json``.  Keeping this in one
    function means the CLI, the API and the tests all agree.
    """

    env = os.environ.get("JOBFLOW_VAULT")
    if env:
        return Path(env).expanduser()
    return Path.cwd() / DEFAULT_VAULT_NAME


class Vault:
    """An in-memory job-hunt vault with an atomic JSON backing file."""

    def __init__(
        self,
        applications: Optional[Iterable[Application]] = None,
        owner: str = "",
        path: Optional[Path] = None,
    ) -> None:
        self.owner = clean_text(owner, "owner", max_len=80)
        self.path: Optional[Path] = Path(path) if path else None
        self.applications: List[Application] = []
        for app in applications or []:
            self.add(app, save=False)

    # -- collection access -------------------------------------------------

    def __len__(self) -> int:
        return len(self.applications)

    def __iter__(self):
        return iter(self.applications)

    def __contains__(self, app_id: object) -> bool:
        return any(a.id == app_id for a in self.applications)

    def ids(self) -> List[str]:
        return [a.id for a in self.applications]

    def get(self, app_id: str) -> Application:
        """Fetch by id, or by unique case-insensitive prefix.

        Prefix lookup makes the CLI pleasant (``log acme-swe``) without giving
        up safety: an ambiguous prefix raises rather than guessing.
        """

        wanted = clean_text(app_id, "id", required=True, max_len=80).lower()
        for app in self.applications:
            if app.id == wanted:
                return app
        matches = [a for a in self.applications if a.id.startswith(wanted)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(sorted(a.id for a in matches))
            raise ValidationError(f"id {app_id!r} is ambiguous — matches: {names}")
        known = ", ".join(sorted(self.ids())) or "(vault is empty)"
        raise NotFoundError(f"no application with id {app_id!r}. Known ids: {known}")

    def find(self, app_id: str) -> Optional[Application]:
        try:
            return self.get(app_id)
        except JobFlowError:
            return None

    # -- mutation ----------------------------------------------------------

    def add(self, app: Application, save: bool = False) -> Application:
        if app.id in self:
            raise ValidationError(f"an application with id {app.id!r} already exists")
        self.applications.append(app)
        if save:
            self.save()
        return app

    def remove(self, app_id: str, save: bool = False) -> Application:
        app = self.get(app_id)
        self.applications = [a for a in self.applications if a.id != app.id]
        if save:
            self.save()
        return app

    def sort(self) -> "Vault":
        """Canonical ordering: open work first, then newest activity."""

        def key(app: Application):
            closed_rank = 1 if app.is_closed else 0
            last = app.last_activity_on()
            return (closed_rank, -app.priority, -(last.toordinal() if last else 0), app.id)

        self.applications.sort(key=key)
        return self

    def filter(
        self,
        statuses: Optional[Iterable[str]] = None,
        active_only: bool = False,
        tag: Optional[str] = None,
        company: Optional[str] = None,
    ) -> List[Application]:
        wanted = set(statuses) if statuses else None
        out: List[Application] = []
        for app in self.applications:
            if wanted is not None and app.status not in wanted:
                continue
            if active_only and not app.is_active:
                continue
            if tag and tag.lower() not in app.tags:
                continue
            if company and company.lower() not in app.company.lower():
                continue
            out.append(app)
        return out

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "owner": self.owner,
            "applications": [a.to_dict() for a in self.applications],
        }

    @classmethod
    def from_dict(cls, data: Any, path: Optional[Path] = None) -> "Vault":
        if not isinstance(data, dict):
            raise ValidationError(f"vault must be a JSON object, got {type(data).__name__}")
        version = data.get("schema_version", SCHEMA_VERSION)
        if version != SCHEMA_VERSION:
            raise ValidationError(
                f"unsupported vault schema_version {version!r} (expected {SCHEMA_VERSION})"
            )
        raw_apps = data.get("applications")
        if raw_apps is None:
            raw_apps = []
        if not isinstance(raw_apps, list):
            raise ValidationError("vault 'applications' must be a list")
        vault = cls(owner=data.get("owner", ""), path=path)
        seen: List[str] = []
        for entry in raw_apps:
            app = Application.from_dict(entry)
            if app.id in seen:
                raise ValidationError(f"duplicate application id in vault: {app.id!r}")
            seen.append(app.id)
            vault.applications.append(app)
        return vault

    # -- disk I/O ----------------------------------------------------------

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Vault":
        """Read a vault.  A missing file yields an empty vault, not an error."""

        target = Path(path) if path else default_vault_path()
        if not target.exists():
            return cls(path=target)
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise JobFlowError(f"cannot read vault at {target}: {exc}") from exc
        if not raw.strip():
            return cls(path=target)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValidationError(
                f"vault at {target} is not valid JSON (line {exc.lineno}, column {exc.colno}): "
                f"{exc.msg}"
            ) from exc
        return cls.from_dict(data, path=target)

    def save(self, path: Optional[Path] = None) -> Path:
        """Write atomically: a crash mid-write leaves the old vault intact."""

        target = Path(path) if path else self.path
        if target is None:
            raise JobFlowError("no vault path set; pass a path or use Vault.load()")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.to_dict(), indent=2, ensure_ascii=False, sort_keys=False)
        payload += "\n"

        fd, tmp_name = tempfile.mkstemp(
            prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, target)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        self.path = target
        try:  # Best effort: the vault holds personal data.
            os.chmod(target, 0o600)
        except OSError:
            pass
        return target

    # -- convenience -------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Cheap counts, no analytics import (avoids a cycle)."""

        by_status: Dict[str, int] = {}
        for app in self.applications:
            by_status[app.status] = by_status.get(app.status, 0) + 1
        return {
            "total": len(self.applications),
            "active": sum(1 for a in self.applications if a.is_active),
            "closed": sum(1 for a in self.applications if a.is_closed),
            "wishlist": sum(1 for a in self.applications if a.status == "wishlist"),
            "by_status": dict(sorted(by_status.items())),
            "owner": self.owner,
        }
