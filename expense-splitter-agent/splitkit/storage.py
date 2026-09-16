"""Group persistence: atomic JSON reads and writes.

A group file is the whole state of a SplitKit group — members, expenses and
recorded settlements — and it is plain, diffable JSON so it can be committed
next to a trip's photos or emailed to the group.

Writes go through a temporary file plus ``os.replace``, which is atomic on
POSIX: a crash mid-write leaves the previous good group file intact rather
than a truncated one. That matters because losing a group file loses the
ledger, and the ledger is the product.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from .errors import ValidationError
from .models import SCHEMA_VERSION, Group

#: Directory holding the default group file when none is specified.
DEFAULT_DIR = ".splitkit"
DEFAULT_FILENAME = "group.json"


def resolve_group_path(path: Optional[str] = None, *, cwd: Optional[Path] = None) -> Path:
    """Return the group file path, expanding ``~`` and defaulting sensibly."""
    if path:
        return Path(path).expanduser()
    base = cwd or Path.cwd()
    return base / DEFAULT_DIR / DEFAULT_FILENAME


def save_group(group: Group, path: Optional[str] = None) -> Path:
    """Write a group to disk atomically. Returns the path written."""
    target = resolve_group_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    payload = group.to_dict()
    payload["schema_version"] = SCHEMA_VERSION
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False) + "\n"

    fd, tmp_name = tempfile.mkstemp(
        dir=str(target.parent), prefix=target.name + ".", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def load_group(path: Optional[str] = None, *, cwd: Optional[Path] = None) -> Group:
    """Read a group file. Raises a helpful error when it is missing or broken."""
    target = resolve_group_path(path, cwd=cwd)
    if not target.is_file():
        raise ValidationError(
            f"no group file at {target}. Create one first:\n"
            f"  splitkit init \"Trip to Hunza\" --members Ali,Sara,Bilal"
        )
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{target} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"{target} must contain a JSON object")

    version = int(raw.get("schema_version") or SCHEMA_VERSION)
    if version > SCHEMA_VERSION:
        raise ValidationError(
            f"{target} uses schema version {version}, but this build understands "
            f"up to {SCHEMA_VERSION}. Upgrade SplitKit to read it."
        )
    return Group.from_dict(raw)


def group_exists(path: Optional[str] = None, *, cwd: Optional[Path] = None) -> bool:
    return resolve_group_path(path, cwd=cwd).is_file()


def export_group(group: Group) -> str:
    """Serialise a group to a compact JSON string (for API responses)."""
    return json.dumps(group.to_dict(), indent=2, ensure_ascii=False)


def import_group(text: str) -> Group:
    """Parse a group from a JSON string."""
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"group JSON is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError("group JSON must be an object")
    return Group.from_dict(raw)


def new_group(
    name: str,
    member_names: List[str],
    *,
    currency: str = "USD",
    group_id: Optional[str] = None,
) -> Group:
    """Build a fresh group from a name and a list of member names."""
    from .models import slugify

    if not member_names:
        raise ValidationError("a group needs at least one member")
    gid = group_id or slugify(name)
    group = Group(id=gid, name=name or gid, currency=currency)
    for member_name in member_names:
        group.add_member(member_name)
    return group


def backup_group(path: Optional[str] = None, *, cwd: Optional[Path] = None) -> Optional[Path]:
    """Copy the current group file to ``<name>.bak``. Returns None if absent."""
    target = resolve_group_path(path, cwd=cwd)
    if not target.is_file():
        return None
    backup = target.with_suffix(target.suffix + ".bak")
    backup.write_bytes(target.read_bytes())
    return backup


__all__ = [
    "DEFAULT_DIR",
    "DEFAULT_FILENAME",
    "resolve_group_path",
    "save_group",
    "load_group",
    "group_exists",
    "export_group",
    "import_group",
    "new_group",
    "backup_group",
]
