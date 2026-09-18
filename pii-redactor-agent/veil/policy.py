"""Policy: what to do with each entity.

A policy is a plain mapping, loadable from the YAML subset in `miniyaml`, a dict,
or the environment. It answers three questions:

1. **Which entities am I looking for?**  ``entities`` enables or disables the
   catalogue; opt-in entities (``PERSON``) stay off until named.
2. **What do I do with each one?**  ``actions`` maps an entity to one of
   ``mask``, ``redact``, ``hash``, ``tokenize``, ``remove`` or ``keep``.
3. **What must never be touched?**  ``allowlist`` holds literal values that are
   reported but left alone; ``denylist`` forces an exact literal to be redacted
   even when no detector recognised it \u2014 the escape hatch for internal formats
   the engine does not know.

Precedence, lowest to highest: built-in defaults, the policy document, then
environment overrides (``VEIL_ACTION_EMAIL=redact`` style).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

from . import miniyaml
from .errors import PolicyError
from .models import ACTION_KEEP, ACTIONS, ALL_ENTITIES, MASK_CHAR, OPT_IN_ENTITIES

__all__ = [
    "ACTION_KEEP", "ACTIONS", "DEFAULT_ACTIONS", "DEFAULT_ENTITIES",
    "ENV_PREFIX", "MASK_CHAR", "Policy", "apply_env", "from_dict",
    "from_yaml", "load", "resolve",
]

#: Out-of-the-box actions. Credentials and financial identifiers are removed
#: outright; contact details are masked so the document stays readable.
DEFAULT_ACTIONS: Dict[str, str] = {
    "SECRET": "remove",
    "CREDIT_CARD": "mask",
    "SSN": "mask",
    "IBAN": "mask",
    "NATIONAL_ID": "mask",
    "EMAIL": "mask",
    "PHONE": "mask",
    "DATE_OF_BIRTH": "mask",
    "IPV4": "mask",
    "IPV6": "mask",
    "MAC": "mask",
    "URL": "redact",
    "PERSON": "redact",
}

DEFAULT_ENTITIES: Sequence[str] = tuple(
    e for e in ALL_ENTITIES if e not in OPT_IN_ENTITIES
)

ENV_PREFIX = "VEIL_"


@dataclass
class Policy:
    """A resolved, validated redaction policy."""

    name: str = "default"
    entities: List[str] = field(default_factory=lambda: list(DEFAULT_ENTITIES))
    actions: Dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ACTIONS))
    keep_first: int = 0
    keep_last: int = 4
    mask_char: str = MASK_CHAR
    hash_salt: str = ""
    token_prefix: str = "VEIL"
    allowlist: List[str] = field(default_factory=list)
    denylist: List[str] = field(default_factory=list)
    #: Extra names for the opt-in PERSON detector.
    names: List[str] = field(default_factory=list)
    case_sensitive_denylist: bool = False
    #: Provenance of the effective policy (a path, "built-in defaults", or a
    #: record of the environment variables that were applied). Metadata only:
    #: deliberately excluded from to_dict so a serialised policy round-trips
    #: through from_dict, which rejects unknown keys.
    source: str = "built-in defaults"

    def action_for(self, entity: str) -> str:
        return self.actions.get(entity, "redact")

    def is_enabled(self, entity: str) -> bool:
        return entity in self.entities

    def allow_set(self) -> Set[str]:
        return {v for v in self.allowlist if v}

    # -- serialisation ---------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "entities": list(self.entities),
            "actions": dict(self.actions),
            "keep_first": self.keep_first,
            "keep_last": self.keep_last,
            "mask_char": self.mask_char,
            "hash_salt": self.hash_salt,
            "token_prefix": self.token_prefix,
            "allowlist": list(self.allowlist),
            "denylist": list(self.denylist),
            "names": list(self.names),
            "case_sensitive_denylist": self.case_sensitive_denylist,
        }


def _as_bool(value: Any, where: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes", "on", "1"):
            return True
        if lowered in ("false", "no", "off", "0"):
            return False
    raise PolicyError(f"{where} must be a boolean, got {value!r}")


def _as_int(value: Any, where: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise PolicyError(f"{where} must be an integer, got {value!r}")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise PolicyError(f"{where} must be an integer, got {value!r}") from None
    if number < minimum:
        raise PolicyError(f"{where} must be >= {minimum}, got {number}")
    return number


def _as_list(value: Any, where: str) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        out: List[str] = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, (dict, list, tuple, set)):
                raise PolicyError(f"{where} entries must be scalars, got {item!r}")
            out.append(str(item))
        return out
    raise PolicyError(f"{where} must be a list or a single value, got {value!r}")


def from_dict(data: Mapping[str, Any], source: str = "document") -> Policy:
    """Build a validated Policy from a parsed mapping."""
    if data is None:
        return Policy(source=source)
    if not isinstance(data, Mapping):
        raise PolicyError(f"policy root must be a mapping, got {type(data).__name__}")

    known = {
        "name", "entities", "actions", "keep_first", "keep_last", "mask_char",
        "hash_salt", "token_prefix", "allowlist", "denylist", "names",
        "case_sensitive_denylist",
    }
    unknown = sorted(set(data.keys()) - known)
    if unknown:
        raise PolicyError(
            f"unknown policy key(s): {', '.join(unknown)}. "
            f"Known keys: {', '.join(sorted(known))}"
        )

    policy = Policy(source=source)

    if "name" in data and data["name"] is not None:
        policy.name = str(data["name"])

    if "entities" in data:
        requested = _as_list(data["entities"], "entities")
        resolved: List[str] = []
        for raw in requested:
            key = raw.strip().upper()
            if key == "ALL":
                resolved.extend(ALL_ENTITIES)
                continue
            if key.startswith("!"):
                target = key[1:]
                _require_entity(target)
                resolved = [e for e in resolved if e != target]
                continue
            _require_entity(key)
            if key not in resolved:
                resolved.append(key)
        if not resolved:
            raise PolicyError("entities resolved to an empty set; nothing would be scanned")
        policy.entities = resolved

    if "actions" in data:
        raw_actions = data["actions"]
        if not isinstance(raw_actions, Mapping):
            raise PolicyError(
                f"actions must be a mapping of ENTITY: action, got {type(raw_actions).__name__}"
            )
        merged = dict(DEFAULT_ACTIONS)
        for raw_entity, raw_action in raw_actions.items():
            entity = str(raw_entity).strip().upper()
            _require_entity(entity)
            action = str(raw_action).strip().lower()
            if action not in ACTIONS:
                raise PolicyError(
                    f"unknown action {action!r} for {entity}. "
                    f"Valid actions: {', '.join(ACTIONS)}"
                )
            merged[entity] = action
        policy.actions = merged

    if "keep_first" in data and data["keep_first"] is not None:
        policy.keep_first = _as_int(data["keep_first"], "keep_first", 0)
    if "keep_last" in data and data["keep_last"] is not None:
        policy.keep_last = _as_int(data["keep_last"], "keep_last", 0)

    if "mask_char" in data and data["mask_char"] is not None:
        char = str(data["mask_char"])
        if len(char) != 1:
            raise PolicyError(f"mask_char must be exactly one character, got {char!r}")
        policy.mask_char = char

    if "hash_salt" in data and data["hash_salt"] is not None:
        policy.hash_salt = str(data["hash_salt"])
    if "token_prefix" in data and data["token_prefix"] is not None:
        prefix = str(data["token_prefix"]).strip()
        if not prefix or not prefix.replace("_", "").isalnum():
            raise PolicyError(
                f"token_prefix must be alphanumeric (underscores allowed), got {prefix!r}"
            )
        policy.token_prefix = prefix

    for key in ("allowlist", "denylist", "names"):
        if key in data:
            setattr(policy, key, _as_list(data[key], key))

    if "case_sensitive_denylist" in data and data["case_sensitive_denylist"] is not None:
        policy.case_sensitive_denylist = _as_bool(
            data["case_sensitive_denylist"], "case_sensitive_denylist"
        )

    if len(policy.mask_char) != 1:
        raise PolicyError(f"mask_char must be exactly one character, got {policy.mask_char!r}")
    return policy


def _require_entity(entity: str) -> None:
    if entity not in ALL_ENTITIES:
        raise PolicyError(
            f"unknown entity {entity!r}. Known entities: {', '.join(ALL_ENTITIES)}"
        )


def from_yaml(source: str) -> Policy:
    """Parse the YAML subset and build a policy."""
    return from_dict(miniyaml.parse(source), source="inline yaml")


def load(path: str) -> Policy:
    """Load a policy from a YAML file."""
    data = miniyaml.load(path)
    return from_dict(data, source=path)


def apply_env(policy: Policy, environ: Optional[Mapping[str, str]] = None) -> Policy:
    """Overlay environment overrides onto a policy.

    Recognised variables:
      - ``VEIL_ENTITIES``               comma-separated entity list (``ALL`` allowed, ``!X`` removes)
      - ``VEIL_ACTION_<ENTITY>``        e.g. ``VEIL_ACTION_EMAIL=redact``
      - ``VEIL_KEEP_FIRST`` / ``VEIL_KEEP_LAST``
      - ``VEIL_MASK_CHAR``
      - ``VEIL_HASH_SALT``
      - ``VEIL_TOKEN_PREFIX``
      - ``VEIL_ALLOWLIST`` / ``VEIL_DENYLIST``  comma-separated literals
      - ``VEIL_NAMES``                  comma-separated extra names for PERSON
    """
    env = os.environ if environ is None else environ
    touched: List[str] = []

    if "VEIL_ENTITIES" in env:
        raw = env["VEIL_ENTITIES"]
        requested = [p.strip() for p in raw.split(",") if p.strip()]
        resolved: List[str] = []
        for item in requested:
            key = item.upper()
            if key == "ALL":
                resolved.extend(ALL_ENTITIES)
                continue
            if key.startswith("!"):
                target = key[1:]
                _require_entity(target)
                resolved = [e for e in resolved if e != target]
                continue
            _require_entity(key)
            if key not in resolved:
                resolved.append(key)
        if not resolved:
            raise PolicyError("VEIL_ENTITIES resolved to an empty set")
        policy.entities = resolved
        touched.append("VEIL_ENTITIES")

    for entity in ALL_ENTITIES:
        var = f"{ENV_PREFIX}ACTION_{entity}"
        if var in env:
            action = env[var].strip().lower()
            if action not in ACTIONS:
                raise PolicyError(
                    f"{var}={action!r} is not a valid action. "
                    f"Valid actions: {', '.join(ACTIONS)}"
                )
            policy.actions[entity] = action
            touched.append(var)

    for key, caster in (("keep_first", _as_int), ("keep_last", _as_int)):
        var = f"{ENV_PREFIX}{key.upper()}"
        if var in env:
            setattr(policy, key, caster(env[var], var, 0))
            touched.append(var)

    if "VEIL_MASK_CHAR" in env:
        char = env["VEIL_MASK_CHAR"]
        if len(char) != 1:
            raise PolicyError(f"VEIL_MASK_CHAR must be one character, got {char!r}")
        policy.mask_char = char
        touched.append("VEIL_MASK_CHAR")

    if "VEIL_HASH_SALT" in env:
        policy.hash_salt = env["VEIL_HASH_SALT"]
        touched.append("VEIL_HASH_SALT")

    if "VEIL_TOKEN_PREFIX" in env:
        prefix = env["VEIL_TOKEN_PREFIX"].strip()
        if not prefix or not prefix.replace("_", "").isalnum():
            raise PolicyError(f"VEIL_TOKEN_PREFIX must be alphanumeric, got {prefix!r}")
        policy.token_prefix = prefix
        touched.append("VEIL_TOKEN_PREFIX")

    for key in ("allowlist", "denylist", "names"):
        var = f"{ENV_PREFIX}{key.upper()}"
        if var in env:
            values = [v.strip() for v in env[var].split(",") if v.strip()]
            setattr(policy, key, values)
            touched.append(var)

    if touched:
        policy.source = f"{policy.source} + env({', '.join(touched)})"
    return policy


def resolve(policy: Optional[Policy] = None, path: Optional[str] = None,
            environ: Optional[Mapping[str, str]] = None) -> Policy:
    """Resolve the effective policy from an object, a file path, and the environment."""
    if policy is not None and path is not None:
        raise PolicyError("pass either a policy object or a path, not both")
    if path is not None:
        resolved = load(path)
    elif policy is not None:
        resolved = policy
    else:
        resolved = Policy()
    return apply_env(resolved, environ)
