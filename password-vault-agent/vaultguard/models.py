"""VAULTGUARD data model.

An entry is the unit of storage: one credential, one login, one secret.
Plaintext never leaves this object except through an explicit ``reveal``.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

CATEGORIES = (
    "login",
    "email",
    "banking",
    "social",
    "work",
    "server",
    "api",
    "wifi",
    "secure-note",
    "other",
)


def utcnow() -> str:
    """Current UTC time as an ISO-8601 string with a ``Z`` suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_ts(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp, tolerating a trailing ``Z``."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def days_since(value: Optional[str]) -> Optional[int]:
    """Whole days elapsed since ``value`` (``None`` when unparseable)."""
    moment = parse_ts(value)
    if moment is None:
        return None
    delta = datetime.now(timezone.utc) - moment
    return max(0, int(delta.total_seconds() // 86400))


def new_id() -> str:
    """Short, collision-resistant entry identifier."""
    return uuid.uuid4().hex[:12]


@dataclass
class VaultEntry:
    """A single stored credential."""

    id: str = field(default_factory=new_id)
    title: str = ""
    username: str = ""
    password: str = ""
    url: str = ""
    notes: str = ""
    tags: List[str] = field(default_factory=list)
    category: str = "login"
    favorite: bool = False
    totp: str = ""
    created_at: str = field(default_factory=utcnow)
    updated_at: str = field(default_factory=utcnow)
    password_updated_at: str = field(default_factory=utcnow)
    password_hint: str = ""

    def __post_init__(self) -> None:
        self.title = (self.title or "").strip()
        self.category = (self.category or "login").strip().lower() or "login"
        if isinstance(self.tags, str):
            self.tags = [tag.strip() for tag in self.tags.split(",") if tag.strip()]
        self.tags = sorted({str(tag).strip().lower() for tag in (self.tags or []) if str(tag).strip()})
        if not self.title:
            self.title = self.username or self.url or "untitled"

    # -- serialisation ----------------------------------------------------- #
    def to_dict(self, reveal: bool = True) -> Dict[str, Any]:
        """Serialise; ``reveal=False`` masks the secret for safe display."""
        data = asdict(self)
        if not reveal:
            data["password"] = mask_secret(self.password)
            data["totp"] = mask_secret(self.totp) if self.totp else ""
        return data

    def to_storage(self) -> Dict[str, Any]:
        """Serialise for the encrypted payload (always full fidelity)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VaultEntry":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        clean = {k: v for k, v in (data or {}).items() if k in known}
        return cls(**clean)

    # -- helpers ----------------------------------------------------------- #
    def age_days(self) -> int:
        """Days since the stored password was last rotated."""
        return days_since(self.password_updated_at) or 0

    def touch_password(self, hint: str) -> None:
        """Record a password rotation and refresh the reuse fingerprint."""
        self.password_hint = hint
        self.password_updated_at = utcnow()
        self.updated_at = self.password_updated_at

    def matches(self, needle: str) -> bool:
        """Case-insensitive search across the human-visible fields."""
        needle = (needle or "").strip().lower()
        if not needle:
            return True
        haystack = " ".join(
            [self.title, self.username, self.url, self.notes, self.category, " ".join(self.tags)]
        ).lower()
        return needle in haystack


def mask_secret(secret: str, keep: int = 3) -> str:
    """Render a secret as ``abc••••••••`` for shoulder-safe display."""
    if not secret:
        return ""
    if len(secret) <= keep:
        return "•" * len(secret)
    return secret[:keep] + "•" * max(4, len(secret) - keep)


def summarise(entries: List[VaultEntry]) -> Dict[str, Any]:
    """Aggregate counts used by the CLI, API and web HUD."""
    categories: Dict[str, int] = {}
    tags: Dict[str, int] = {}
    for entry in entries:
        categories[entry.category] = categories.get(entry.category, 0) + 1
        for tag in entry.tags:
            tags[tag] = tags.get(tag, 0) + 1
    return {
        "total": len(entries),
        "favorites": sum(1 for e in entries if e.favorite),
        "with_url": sum(1 for e in entries if e.url),
        "with_totp": sum(1 for e in entries if e.totp),
        "categories": dict(sorted(categories.items(), key=lambda kv: (-kv[1], kv[0]))),
        "tags": dict(sorted(tags.items(), key=lambda kv: (-kv[1], kv[0]))),
    }
