"""Typed errors.

Every failure VEIL raises on purpose names the offending value, so a bad policy
or a malformed input tells you *what* was wrong rather than failing anonymously.
"""

from __future__ import annotations


class VeilError(Exception):
    """Base class for deliberate errors."""

    code = "veil_error"

    def __init__(self, message: str, **context):
        super().__init__(message)
        self.message = message
        self.context = context

    def to_dict(self) -> dict:
        return {"error": self.code, "message": self.message, "context": self.context}


class PolicyError(VeilError):
    """A policy said something the engine cannot honour."""

    code = "policy_error"


class ConfigError(VeilError):
    """A configuration file could not be parsed."""

    code = "config_error"


class VaultError(VeilError):
    """The token vault is missing, corrupt, or locked."""

    code = "vault_error"


class InputError(VeilError):
    """The input itself is unusable (not text, unreadable, empty where it matters)."""

    code = "input_error"
