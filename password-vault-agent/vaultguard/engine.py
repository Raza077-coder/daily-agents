"""VAULTGUARD engine — the orchestration layer.

Everything the CLI, the REST API and the library share lives here:

* vault lifecycle (create / unlock / lock / rekey)
* entry CRUD with automatic strength + reuse fingerprinting
* search, filtering, sorting and import/export
* deterministic password suggestions and generation
* the security audit

The engine is stateful: ``unlock`` loads the decrypted entries into memory,
``lock`` wipes them. Nothing is persisted except through :meth:`save`, which
always re-encrypts with a fresh nonce.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .audit import AuditReport, VaultAuditor
from .crypto import VaultCryptoError, hash_password_hint
from .generator import generate_passphrase, generate_password, generate_pin, suggest_for
from .models import VaultEntry, days_since, summarise, utcnow
from .storage import (
    DEFAULT_VAULT_PATH,
    VaultFile,
    VaultFormatError,
    backup_vault,
    vault_exists,
)
from .strength import estimate_strength


class VaultError(Exception):
    """Generic engine-level failure."""


class VaultAuthError(VaultError):
    """Wrong master password or a locked vault."""


@dataclass
class SearchQuery:
    """Filter + sort specification for :meth:`VaultEngine.search`."""

    text: str = ""
    category: str = ""
    tag: str = ""
    favorites_only: bool = False
    weak_only: bool = False
    stale_only: bool = False
    sort: str = "title"
    descending: bool = False
    limit: int = 0


SORT_KEYS = {
    "title": lambda e: e.title.lower(),
    "username": lambda e: e.username.lower(),
    "category": lambda e: e.category,
    "age": lambda e: e.age_days(),
    "updated": lambda e: e.updated_at or "",
    "created": lambda e: e.created_at or "",
}


class VaultEngine:
    """High-level façade over :class:`~vaultguard.storage.VaultFile`."""

    def __init__(self, path: str = DEFAULT_VAULT_PATH, *, iterations: Optional[int] = None) -> None:
        self.path = path
        kwargs: Dict[str, Any] = {}
        if iterations:
            kwargs["iterations"] = iterations
        self._file = VaultFile(path, **kwargs)
        self._entries: List[VaultEntry] = []
        self._unlocked = False
        self._master: Optional[str] = None
        self.auditor = VaultAuditor()

    # ------------------------------------------------------------------ #
    # lifecycle
    # ------------------------------------------------------------------ #
    @property
    def is_unlocked(self) -> bool:
        return self._unlocked

    @property
    def entries(self) -> List[VaultEntry]:
        self._require_unlocked()
        return list(self._entries)

    def exists(self) -> bool:
        return vault_exists(self.path)

    def create(self, master_password: str, *, force: bool = False) -> Dict[str, Any]:
        """Create a new vault and leave it unlocked."""
        if self.exists() and not force:
            raise VaultError(f"a vault already exists at {self.path} (use force to overwrite)")
        if len(master_password or "") < 8:
            raise VaultError("master password must be at least 8 characters")
        self._file.create(master_password, entries=[])
        self._unlocked = True
        self._master = master_password
        self._entries = []
        return {"path": self.path, "status": "created", "iterations": self._file.iterations}

    def unlock(self, master_password: str) -> Dict[str, Any]:
        """Decrypt the vault into memory."""
        try:
            payload = self._file.unlock(master_password)
        except VaultCryptoError as exc:
            raise VaultAuthError(str(exc)) from exc
        self._entries = [VaultEntry.from_dict(d) for d in payload.get("entries", [])]
        self._unlocked = True
        self._master = master_password
        return {
            "status": "unlocked",
            "entries": len(self._entries),
            "path": self.path,
            "cipher": self._file.cipher,
        }

    def lock(self) -> Dict[str, Any]:
        """Wipe decrypted material from memory."""
        self._entries = []
        self._master = None
        self._unlocked = False
        return {"status": "locked"}

    def save(self) -> Dict[str, Any]:
        """Persist the in-memory entries (atomic re-encrypt)."""
        self._require_unlocked()
        header = self._file.save(
            self._master or "",
            [entry.to_storage() for entry in self._entries],
        )
        return {
            "status": "saved",
            "entries": len(self._entries),
            "updated_at": header.get("meta", {}).get("updated_at"),
        }

    def change_master_password(self, old: str, new: str) -> Dict[str, Any]:
        if len(new or "") < 8:
            raise VaultError("new master password must be at least 8 characters")
        self._require_unlocked()
        if old != self._master:
            raise VaultAuthError("current master password is incorrect")
        info = self._file.change_master_password(old, new)
        self._master = new
        return {"status": "rekeyed", "path": self.path, "cipher": info.get("cipher")}

    def info(self) -> Dict[str, Any]:
        """Non-secret metadata (works while locked)."""
        if not self.exists():
            return {"path": self.path, "status": "missing"}
        data = self._file.describe()
        data["status"] = "unlocked" if self._unlocked else "locked"
        return data

    # ------------------------------------------------------------------ #
    # CRUD
    # ------------------------------------------------------------------ #
    def add(
        self,
        *,
        title: str,
        password: str = "",
        username: str = "",
        url: str = "",
        category: str = "login",
        notes: str = "",
        tags: Optional[Iterable[str]] = None,
        totp: str = "",
        favorite: bool = False,
        generate: bool = False,
        length: int = 20,
        save: bool = True,
    ) -> Dict[str, Any]:
        """Add an entry, generating a password when asked."""
        self._require_unlocked()
        secret = password or ""
        if generate and not secret:
            secret = suggest_for(length)
        entry = VaultEntry(
            title=title,
            username=username,
            password=secret,
            url=url,
            notes=notes,
            category=category,
            tags=list(tags or []),
            totp=totp,
            favorite=favorite,
        )
        entry.password_hint = hash_password_hint(secret) if secret else ""
        self._entries.append(entry)
        if save:
            self.save()
        return {"entry": entry.to_dict(reveal=False), "generated": bool(generate and secret)}

    def update(self, entry_id: str, *, save: bool = True, **changes: Any) -> Dict[str, Any]:
        """Patch fields on an entry; rotating the password refreshes fingerprints."""
        self._require_unlocked()
        entry = self._find(entry_id)
        password_changed = False
        for key, value in changes.items():
            if value is None or key in ("id", "created_at"):
                continue
            if key in ("password", "totp"):
                if value != getattr(entry, key):
                    setattr(entry, key, value)
                    password_changed = True
                continue
            if key == "tags":
                entry.tags = [str(t).strip().lower() for t in value if str(t).strip()]
                continue
            if hasattr(entry, key):
                setattr(entry, key, value)
        if password_changed:
            entry.password_hint = hash_password_hint(entry.password) if entry.password else ""
            entry.touch_password(entry.password_hint)
        else:
            entry.updated_at = utcnow()
        if save:
            self.save()
        return {"entry": entry.to_dict(reveal=False), "password_changed": password_changed}

    def delete(self, entry_id: str, *, save: bool = True) -> Dict[str, Any]:
        self._require_unlocked()
        entry = self._find(entry_id)
        self._entries = [e for e in self._entries if e.id != entry_id]
        if save:
            self.save()
        return {"status": "deleted", "entry": entry.to_dict(reveal=False)}

    def get(self, entry_id: str, *, reveal: bool = False) -> Dict[str, Any]:
        self._require_unlocked()
        return self._find(entry_id).to_dict(reveal=reveal)

    def reveal(self, entry_id: str) -> Dict[str, Any]:
        """Explicitly return the plaintext secret for one entry."""
        self._require_unlocked()
        entry = self._find(entry_id)
        return {
            "id": entry.id,
            "title": entry.title,
            "username": entry.username,
            "password": entry.password,
            "totp": entry.totp,
        }

    # ------------------------------------------------------------------ #
    # search / listing
    # ------------------------------------------------------------------ #
    def search(self, query: Optional[SearchQuery] = None, **kwargs: Any) -> List[Dict[str, Any]]:
        """Filter, sort and (optionally) mask entries."""
        self._require_unlocked()
        query = query or SearchQuery(**kwargs)
        results = list(self._entries)

        if query.text:
            results = [e for e in results if e.matches(query.text)]
        if query.category:
            results = [e for e in results if e.category == query.category.strip().lower()]
        if query.tag:
            tag = query.tag.strip().lower()
            results = [e for e in results if tag in e.tags]
        if query.favorites_only:
            results = [e for e in results if e.favorite]
        if query.weak_only:
            results = [e for e in results if estimate_strength(e.password, reused=self._is_reused(e)).score < 55]
        if query.stale_only:
            results = [e for e in results if e.age_days() > 365]

        key = SORT_KEYS.get(query.sort, SORT_KEYS["title"])
        results.sort(key=key, reverse=query.descending)
        if query.limit:
            results = results[: query.limit]
        return [e.to_dict(reveal=False) for e in results]

    def list_all(self, *, reveal: bool = False) -> List[Dict[str, Any]]:
        self._require_unlocked()
        return [e.to_dict(reveal=reveal) for e in self._entries]

    def stats(self) -> Dict[str, Any]:
        self._require_unlocked()
        data = summarise(self._entries)
        data["vault"] = self.info()
        return data

    def duplicates(self) -> List[Dict[str, Any]]:
        """Entries grouped by identical password fingerprint."""
        self._require_unlocked()
        groups: Dict[str, List[VaultEntry]] = {}
        for entry in self._entries:
            if entry.password_hint:
                groups.setdefault(entry.password_hint, []).append(entry)
        return [
            {"count": len(group), "titles": [e.title for e in group], "ids": [e.id for e in group]}
            for group in groups.values()
            if len(group) > 1
        ]

    # ------------------------------------------------------------------ #
    # generation / analysis
    # ------------------------------------------------------------------ #
    def suggest(self, length: int = 20, *, symbols: bool = True, avoid_ambiguous: bool = False) -> Dict[str, Any]:
        """Generate a strong password plus its strength verdict."""
        password = generate_password(
            length,
            use_symbols=symbols,
            avoid_ambiguous=avoid_ambiguous,
        )
        return {"password": password, "strength": estimate_strength(password).to_dict()}

    def generate(
        self,
        kind: str = "password",
        *,
        length: int = 20,
        words: int = 4,
        separator: str = "-",
    ) -> Dict[str, Any]:
        """Generate a password, passphrase or PIN."""
        if kind == "passphrase":
            secret = generate_passphrase(words, separator=separator, add_number=True)
        elif kind == "pin":
            secret = generate_pin(length)
        else:
            secret = generate_password(length)
        return {"kind": kind, "secret": secret, "strength": estimate_strength(secret).to_dict()}

    def check(self, password: str, *, age_days: int = 0) -> Dict[str, Any]:
        """Score an arbitrary password without storing it."""
        return estimate_strength(password, age_days=age_days).to_dict()

    def audit(self) -> AuditReport:
        self._require_unlocked()
        return self.auditor.audit(self._entries)

    def health_score(self) -> int:
        return self.audit().score

    # ------------------------------------------------------------------ #
    # import / export / backup
    # ------------------------------------------------------------------ #
    def export_plain(self, destination: str, *, include_passwords: bool = True) -> str:
        """Export entries as plain JSON — for backups only, never for sharing."""
        self._require_unlocked()
        payload = {
            "format": "vaultguard-export",
            "exported_at": utcnow(),
            "note": "UNENCRYPTED EXPORT — delete after use" if include_passwords else "masked export",
            "entries": [e.to_dict(reveal=include_passwords) for e in self._entries],
        }
        with open(destination, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, ensure_ascii=False)
        os.chmod(destination, 0o600)
        return destination

    def import_plain(self, source: str, *, save: bool = True) -> Dict[str, Any]:
        """Import entries from a plain JSON export (or a bare list)."""
        self._require_unlocked()
        with open(source, "r", encoding="utf-8") as handle:
            document = json.load(handle)
        raw = document.get("entries") if isinstance(document, dict) else document
        if not isinstance(raw, list):
            raise VaultError("import file does not contain an entries list")

        existing = {e.id for e in self._entries}
        added = 0
        for item in raw:
            entry = VaultEntry.from_dict(item)
            if entry.id in existing:
                entry.id = VaultEntry().id
            entry.password_hint = hash_password_hint(entry.password) if entry.password else ""
            self._entries.append(entry)
            existing.add(entry.id)
            added += 1
        if save:
            self.save()
        return {"status": "imported", "added": added, "total": len(self._entries)}

    def backup(self, destination: str) -> str:
        """Copy the encrypted vault file (stays locked)."""
        return backup_vault(self.path, destination)

    def destroy(self, *, confirm: bool = False) -> Dict[str, Any]:
        """Delete the vault file (requires ``confirm=True``)."""
        if not confirm:
            raise VaultError("destroy requires explicit confirmation")
        if not self.exists():
            raise VaultError(f"no vault found at {self.path}")
        os.remove(self.path)
        self.lock()
        return {"status": "destroyed", "path": self.path}

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #
    def _require_unlocked(self) -> None:
        if not self._unlocked:
            raise VaultAuthError("vault is locked — unlock it with the master password first")

    def _find(self, entry_id: str) -> VaultEntry:
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        # allow lookup by exact title as a convenience
        for entry in self._entries:
            if entry.title.lower() == (entry_id or "").lower():
                return entry
        raise VaultError(f"no entry matching '{entry_id}'")

    def _is_reused(self, entry: VaultEntry) -> bool:
        if not entry.password_hint:
            return False
        return sum(1 for e in self._entries if e.password_hint == entry.password_hint) > 1

    def __enter__(self) -> "VaultEngine":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.lock()

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        state = "unlocked" if self._unlocked else "locked"
        return f"<VaultEngine path={self.path!r} entries={len(self._entries)} {state}>"


def temp_vault_path() -> str:
    """Create an isolated temp path for tests and demos."""
    handle, path = tempfile.mkstemp(prefix="vaultguard-", suffix=".json")
    os.close(handle)
    os.unlink(path)
    return path
