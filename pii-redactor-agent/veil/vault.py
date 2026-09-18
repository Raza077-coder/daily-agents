"""The token vault: the only reversible part of VEIL.

`tokenize` replaces a value with a placeholder and records value -> token so the
document can be restored later. That record is a file containing **the very data
you were trying to remove**, so it is treated as a secret in its own right:

- written with mode ``0600`` (owner read/write only);
- written atomically (temp file + rename) so an interrupted save cannot leave a
  half-written vault that silently restores the wrong values;
- versioned with a format marker so a future change can refuse an old file
  rather than misread it;
- never created implicitly \u2014 if `detokenize` is asked for a vault that is not
  there, it says so instead of returning the tokens unchanged.

The file is JSON so it can be audited with ordinary tools, and every entry keeps
its entity label, which is what lets `detokenize` restore deterministically even
when two values share a token index across different entities.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional

from .errors import VaultError

VAULT_FORMAT = "veil-vault/1"
VAULT_EXTENSION = ".veilvault.json"


@dataclass
class Vault:
    """A token <-> value mapping, plus enough metadata to audit it."""

    format: str = VAULT_FORMAT
    policy: str = "default"
    entries: List[dict] = field(default_factory=list)
    created: str = ""

    # -- construction ----------------------------------------------------

    @staticmethod
    def from_mapping(mapping: Dict[str, str], policy_name: str = "default",
                     entities: Optional[Dict[str, str]] = None,
                     created: str = "") -> "Vault":
        """Build from a `token -> value` mapping.

        ``entities`` may supply a `token -> entity` map so a restored value can
        be reported with its type; without it the entity is derived from the
        token's own shape (`VEIL_EMAIL_001` -> `EMAIL`).
        """
        entity_map = entities or {}
        entries: List[dict] = []
        for token in sorted(mapping):
            value = mapping[token]
            entity = entity_map.get(token) or _entity_from_token(token)
            entries.append({"token": token, "value": value, "entity": entity})
        return Vault(policy=policy_name, entries=entries, created=created)

    # -- access ----------------------------------------------------------

    def to_mapping(self) -> Dict[str, str]:
        return {entry["token"]: entry["value"] for entry in self.entries}

    def entity_of(self, token: str) -> str:
        for entry in self.entries:
            if entry["token"] == token:
                return entry.get("entity") or _entity_from_token(token)
        return _entity_from_token(token)

    def __len__(self) -> int:
        return len(self.entries)

    def __bool__(self) -> bool:
        return bool(self.entries)

    def summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for entry in self.entries:
            key = entry.get("entity") or "UNKNOWN"
            counts[key] = counts.get(key, 0) + 1
        return dict(sorted(counts.items()))

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "format": self.format,
            "policy": self.policy,
            "created": self.created,
            "count": len(self.entries),
            "entries": [
                {"token": e["token"], "value": e["value"],
                 "entity": e.get("entity") or _entity_from_token(e["token"])}
                for e in self.entries
            ],
        }

    def save(self, path: str) -> str:
        """Write atomically with owner-only permissions."""
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)

        descriptor, temp_path = tempfile.mkstemp(
            prefix=".veil-vault-", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, indent=2, sort_keys=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
        except OSError as exc:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            raise VaultError(f"could not write vault to {path}: {exc}") from exc
        return path

    @staticmethod
    def load(path: str) -> "Vault":
        """Read a vault, refusing anything that is not one."""
        if not os.path.exists(path):
            raise VaultError(
                f"no vault at {path}. The vault file is what makes tokenized output "
                f"reversible; without it the tokens cannot be restored."
            )
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise VaultError(f"{path} is not valid JSON: {exc}") from exc
        except OSError as exc:
            raise VaultError(f"could not read {path}: {exc}") from exc

        if not isinstance(data, dict):
            raise VaultError(f"{path} must contain a JSON object at the top level")
        marker = data.get("format")
        if marker != VAULT_FORMAT:
            raise VaultError(
                f"{path} is not a VEIL vault (expected format {VAULT_FORMAT!r}, "
                f"found {marker!r})"
            )
        entries = data.get("entries")
        if not isinstance(entries, list):
            raise VaultError(f"{path} is missing its 'entries' list")
        for entry in entries:
            if not isinstance(entry, dict) or "token" not in entry or "value" not in entry:
                raise VaultError(
                    f"{path} has a malformed entry (each needs 'token' and 'value'): {entry!r}"
                )
        return Vault(
            format=marker,
            policy=data.get("policy", "default"),
            entries=entries,
            created=data.get("created", ""),
        )


def _entity_from_token(token: str) -> str:
    """Recover the entity from a token like ``VEIL_CREDIT_CARD_003``.

    Splits on the trailing index and takes everything between the prefix and it,
    which matters for two-word entities such as ``CREDIT_CARD``.
    """
    parts = token.split("_")
    if len(parts) < 3:
        return "UNKNOWN"
    return "_".join(parts[1:-1]) or "UNKNOWN"


def default_vault_path(document_path: str) -> str:
    """`report.txt` -> `report.txt.veilvault.json`."""
    return f"{document_path}{VAULT_EXTENSION}"


def merge(vaults: Iterable[Vault]) -> Vault:
    """Combine vaults, last write winning for a duplicate token."""
    merged: Dict[str, dict] = {}
    policy_name = "default"
    created = ""
    for vault in vaults:
        policy_name = vault.policy or policy_name
        created = vault.created or created
        for entry in vault.entries:
            merged[entry["token"]] = entry
    return Vault(
        policy=policy_name,
        entries=[merged[key] for key in sorted(merged)],
        created=created,
    )
