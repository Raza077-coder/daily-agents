"""Actions: the transforms applied to a detected value.

Six actions, each with a distinct purpose:

======== =====================================================================
Action   Effect
======== =====================================================================
mask     Replace the middle with a repeated character, keeping the first and
         last few characters so a human can still tell *which* value it was
         ("alice@example.com" -> "al\u2022\u2022\u2022\u2022\u2022\u2022\u2022@example.com").
redact   Replace the whole value with ``[ENTITY]``. Nothing survives.
remove   Delete the value, leaving the surrounding punctuation intact.
hash     Replace with a short HMAC-SHA256 digest, stable for a given salt.
         Two occurrences of the same value hash identically, so the document
         stays linkable without being reversible.
tokenize Replace with a sequential placeholder (``VEIL_EMAIL_001``) and record
         the mapping in the vault. The only reversible action.
keep     Do nothing, but still report the finding.
======== =====================================================================

``hash`` and ``tokenize`` are the interesting pair. Hashing preserves *equality*
across documents, which is what you want when correlating logs; tokenizing
preserves *readability*, which is what you want when handing a document to a
human who may later need the original.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Dict, List, Optional

from .errors import VeilError
from .models import ACTION_KEEP, ACTIONS, ALL_ENTITIES, MASK_CHAR

#: How many hex characters of the digest survive. Twelve keeps collisions
#: vanishingly unlikely while staying short enough to read.
HASH_LENGTH = 12

#: Shortest value worth partially preserving. Below this, masking would reveal
#: so much of the original that the mask is theatre.
MIN_PARTIAL_LENGTH = 8


def _validate_action(action: str) -> str:
    normalised = (action or "").strip().lower()
    if normalised not in ACTIONS:
        raise VeilError(
            f"unknown action {action!r}; valid actions are "
            f"{', '.join(ACTIONS)}"
        )
    return normalised


def mask(value: str, entity: str, keep_first: int = 0, keep_last: int = 4,
         mask_char: str = MASK_CHAR) -> str:
    """Keep a recognisable prefix/suffix and mask everything between.

    When the value is short enough that the kept characters would expose most of
    it, the whole value is masked instead \u2014 a mask that leaks 80% of a secret is
    worse than no mask, because it looks safe.
    """
    if len(value) <= keep_first + keep_last or len(value) < MIN_PARTIAL_LENGTH:
        return mask_char * max(len(value), 1)

    head = value[:keep_first] if keep_first else ""
    tail = value[-keep_last:] if keep_last else ""
    hidden = len(value) - len(head) - len(tail)
    if hidden < 3:
        return mask_char * len(value)
    return f"{head}{mask_char * hidden}{tail}"


def redact(value: str, entity: str) -> str:
    """Replace the entire value with a bracketed label."""
    return f"[{entity}]"


def remove(value: str, entity: str) -> str:
    """Replace the value with nothing."""
    return ""


def hash_value(value: str, salt: str = "", length: int = HASH_LENGTH) -> str:
    """Keyed digest of the value, so equal inputs give equal output.

    HMAC rather than a bare hash: an unsalted SHA of a phone number is trivially
    brute-forced from the (tiny) space of phone numbers, but a keyed digest is
    not without the salt.
    """
    key = (salt or "").encode("utf-8")
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:length]


def hash_replacement(value: str, entity: str, salt: str = "") -> str:
    return f"[{entity}:{hash_value(value, salt)}]"


class Tokenizer:
    """Deterministic sequential token generator.

    Tokens are numbered per entity in first-seen order, so the same document
    always produces the same tokens without persisting a counter. The numbering
    is what the vault records, and it is the only thing that makes an output
    reversible.
    """

    def __init__(self, prefix: str = "VEIL") -> None:
        self.prefix = prefix or "VEIL"
        self._counters: Dict[str, int] = {}
        self._tokens: Dict[str, str] = {}
        self._values: Dict[str, str] = {}

    def token_for(self, entity: str, value: str) -> str:
        if value in self._tokens:
            return self._tokens[value]
        index = self._counters.get(entity, 0) + 1
        self._counters[entity] = index
        token = f"{self.prefix}_{entity}_{index:03d}"
        self._tokens[value] = token
        self._values[token] = value
        return token

    @property
    def mapping(self) -> Dict[str, str]:
        """Token -> original value."""
        return dict(self._values)

    @property
    def by_value(self) -> Dict[str, str]:
        """Original value -> token."""
        return dict(self._tokens)

    def reset(self) -> None:
        self._counters.clear()
        self._tokens.clear()
        self._values.clear()


def apply_action(action: str, value: str, entity: str, *,
                 tokenizer: Optional[Tokenizer] = None,
                 keep_first: int = 0, keep_last: int = 4,
                 mask_char: str = MASK_CHAR, salt: str = "") -> str:
    """Dispatch one action and return the replacement text."""
    resolved = _validate_action(action)
    if resolved == "keep":
        return value
    if resolved == "mask":
        return mask(value, entity, keep_first, keep_last, mask_char)
    if resolved == "redact":
        return redact(value, entity)
    if resolved == "remove":
        return remove(value, entity)
    if resolved == "hash":
        return hash_replacement(value, entity, salt)
    if resolved == "tokenize":
        if tokenizer is None:
            raise VeilError("tokenize action requires a Tokenizer instance")
        return tokenizer.token_for(entity, value)
    raise VeilError(f"action {resolved!r} is not implemented")


def describe_actions() -> List[dict]:
    """Reference table for the docs and the web HUD."""
    return [
        {
            "action": "mask",
            "reversible": False,
            "summary": "Keep a short prefix and suffix, mask the middle.",
            "example": "alice@example.com -> al\u2022\u2022\u2022\u2022\u2022\u2022\u2022@example.com",
            "use_when": "A human must still tell which value it was.",
        },
        {
            "action": "redact",
            "reversible": False,
            "summary": "Replace the whole value with [ENTITY].",
            "example": "4111 1111 1111 1111 -> [CREDIT_CARD]",
            "use_when": "Nothing about the value may survive.",
        },
        {
            "action": "remove",
            "reversible": False,
            "summary": "Delete the value, keep the surrounding text.",
            "example": "api_key=AKIA... -> api_key=",
            "use_when": "A leaked credential must leave no trace at all.",
        },
        {
            "action": "hash",
            "reversible": False,
            "summary": "Replace with a keyed digest, stable for a given salt.",
            "example": "+92 300 1234567 -> [PHONE:9f2c4a1b0d3e]",
            "use_when": "Equal values must stay matchable across documents.",
        },
        {
            "action": "tokenize",
            "reversible": True,
            "summary": "Replace with a sequential placeholder and store the mapping.",
            "example": "alice@example.com -> VEIL_EMAIL_001",
            "use_when": "The document may need to be restored later.",
        },
        {
            "action": "keep",
            "reversible": True,
            "summary": "Leave the value alone, but still report it.",
            "example": "support@company.com -> support@company.com",
            "use_when": "A published address is intentionally public.",
        },
    ]


__all__ = [
    "ACTION_KEEP", "ACTIONS", "ALL_ENTITIES", "HASH_LENGTH",
    "MIN_PARTIAL_LENGTH", "Tokenizer", "apply_action", "describe_actions",
    "hash_replacement", "hash_value", "mask", "redact", "remove",
]
