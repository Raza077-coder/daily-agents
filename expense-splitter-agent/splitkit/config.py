"""Runtime configuration for SplitKit.

Values resolve in this order, later winning:

1. built-in defaults (:data:`DEFAULTS`)
2. ``splitkit.json`` or ``.splitkit.json`` found by walking up from the cwd
3. environment variables prefixed ``SPLITKIT_``
4. an explicit ``overrides`` mapping

Only a handful of knobs exist, and the important ones have honest names:
``default_currency``, ``default_split``, ``min_transfer``, ``rounding``.
Everything is validated on read — a typo in the config file raises with the
offending key named, rather than being silently ignored.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .errors import ValidationError
from .models import normalise_currency
from .splits import MODES

CONFIG_FILENAMES = ("splitkit.json", ".splitkit.json")
ENV_PREFIX = "SPLITKIT_"

DEFAULTS: Dict[str, Any] = {
    "default_currency": "USD",
    "default_split": "equal",
    "min_transfer": "0",           # suppress transfers at or below this amount
    "rounding": "largest_remainder",
    "group_file": ".splitkit/group.json",
    "show_symbols": True,
    "grouping": True,              # thousands separators in output
    "settle_strategy": "optimal",  # greedy | optimal | compare
    "persona_file": "",            # optional overrides for the agent voice
}

#: Keys whose values must be one of a fixed set.
_CHOICES: Dict[str, tuple] = {
    "default_split": MODES,
    "rounding": ("largest_remainder",),
    "settle_strategy": ("greedy", "optimal", "compare"),
}

_BOOL_KEYS = ("show_symbols", "grouping")


@dataclass
class Config:
    """Resolved SplitKit settings."""

    default_currency: str = "USD"
    default_split: str = "equal"
    min_transfer: str = "0"
    rounding: str = "largest_remainder"
    group_file: str = ".splitkit/group.json"
    show_symbols: bool = True
    grouping: bool = True
    settle_strategy: str = "optimal"
    persona_file: str = ""
    source_files: list = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "default_currency": self.default_currency,
            "default_split": self.default_split,
            "min_transfer": self.min_transfer,
            "rounding": self.rounding,
            "group_file": self.group_file,
            "show_symbols": self.show_symbols,
            "grouping": self.grouping,
            "settle_strategy": self.settle_strategy,
            "persona_file": self.persona_file,
        }


def _coerce(key: str, value: Any) -> Any:
    """Validate and normalise one setting."""
    if key == "default_currency":
        return normalise_currency(str(value))
    if key in _CHOICES:
        text = str(value).strip().lower()
        if text not in _CHOICES[key]:
            raise ValidationError(
                f"config {key!r} must be one of {', '.join(_CHOICES[key])} — got {value!r}"
            )
        return text
    if key in _BOOL_KEYS:
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "on"):
            return True
        if text in ("0", "false", "no", "off"):
            return False
        raise ValidationError(f"config {key!r} must be true or false — got {value!r}")
    if key in ("min_transfer", "group_file", "persona_file"):
        return str(value)
    return value


def find_config_file(start: Optional[Path] = None) -> Optional[Path]:
    """Walk up from ``start`` (default cwd) looking for a config file."""
    current = (start or Path.cwd()).resolve()
    for directory in [current, *current.parents]:
        for name in CONFIG_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def load_config(
    path: Optional[str] = None,
    *,
    overrides: Optional[Mapping[str, Any]] = None,
    use_env: bool = True,
    start: Optional[Path] = None,
) -> Config:
    """Build a :class:`Config` from files, environment, and overrides."""
    merged: Dict[str, Any] = dict(DEFAULTS)
    sources: list = []

    explicit = Path(path) if path else None
    if explicit is not None:
        if not explicit.is_file():
            raise ValidationError(f"config file not found: {explicit}")
        file_path = explicit
    else:
        file_path = find_config_file(start)

    if file_path is not None:
        try:
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValidationError(f"{file_path} is not valid JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise ValidationError(f"{file_path} must contain a JSON object")
        merged.update(raw)
        sources.append(str(file_path))

    if use_env:
        for key in list(DEFAULTS):
            env_key = ENV_PREFIX + key.upper()
            if env_key in os.environ:
                merged[key] = os.environ[env_key]
                sources.append(f"env:{env_key}")

    if overrides:
        for key, value in overrides.items():
            if value is None:
                continue
            merged[key] = value
            sources.append(f"override:{key}")

    known = set(DEFAULTS)
    extra = {k: v for k, v in merged.items() if k not in known}
    coerced = {k: _coerce(k, merged[k]) for k in known if k in merged}
    return Config(**coerced, source_files=sources, extra=extra)


def default_persona() -> Dict[str, Any]:
    """SplitKit's voice, used by the CLI and the engine facade.

    Kept in code as well as in ``persona.json`` so the engine never depends on
    a data file being present next to it.
    """
    return {
        "name": "SplitKit",
        "tagline": "Shared costs, settled exactly.",
        "voice": "precise, friendly, brief",
        "phrases": {
            "balanced": [
                "Everyone is square. Nothing to settle.",
                "The book is even — no transfers needed.",
            ],
            "settle": [
                "{n} transfer{s} clears the whole book.",
                "Settle it in {n} payment{s}.",
            ],
            "saved": [
                "That is {saved} fewer payment{s} than the naive plan.",
                "{saved} transfer{s} saved by grouping who owes whom.",
            ],
        },
        "principles": [
            "Never round money away — every minor unit is accounted for.",
            "State the number, then the reason. No hedging.",
            "Refuse an inconsistent split rather than guessing at intent.",
        ],
    }


def load_persona(path: Optional[str] = None) -> Dict[str, Any]:
    """Return the persona dict, merged over the built-in default."""
    persona = default_persona()
    if not path:
        return persona
    p = Path(path)
    if not p.is_file():
        raise ValidationError(f"persona file not found: {p}")
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{p} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValidationError(f"{p} must contain a JSON object")
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(persona.get(key), dict):
            merged = dict(persona[key])
            merged.update(value)
            persona[key] = merged
        else:
            persona[key] = value
    return persona


__all__ = [
    "Config",
    "DEFAULTS",
    "CONFIG_FILENAMES",
    "ENV_PREFIX",
    "load_config",
    "find_config_file",
    "load_persona",
    "default_persona",
]
