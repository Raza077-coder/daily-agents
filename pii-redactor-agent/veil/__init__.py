"""VEIL \u2014 deterministic PII redaction.

Finds personal and secret data in text, replaces it under a policy you control,
and then re-scans its own output to prove nothing survived.

The whole engine is the Python standard library. No network calls, no API keys,
no model, no clock, no randomness: the same input under the same policy always
produces byte-identical output, which is what makes the result reviewable.

    >>> from veil import Scanner
    >>> result = Scanner().redact("reach me at alice@example.com")
    >>> result.text
    'reach me at al\u2022\u2022\u2022\u2022\u2022\u2022\u2022@example.com'
    >>> result.verification.clean
    True
"""

from __future__ import annotations

from .errors import ConfigError, InputError, PolicyError, VaultError, VeilError
from .models import (
    ALL_ENTITIES,
    ENTITY_LABELS,
    ENTITY_VALIDATORS,
    OPT_IN_ENTITIES,
    RISK_WEIGHTS,
    Finding,
    RedactionResult,
    RiskSummary,
    Span,
    Verification,
    catalogue,
)
from .policy import Policy, apply_env, from_yaml
from .scanner import Scanner, build_vault, resolve_spans, score_risk, verify
from .vault import Vault

__version__ = "1.0.0"

__all__ = [
    "ALL_ENTITIES",
    "ConfigError",
    "ENTITY_LABELS",
    "ENTITY_VALIDATORS",
    "Finding",
    "InputError",
    "OPT_IN_ENTITIES",
    "Policy",
    "PolicyError",
    "RISK_WEIGHTS",
    "RedactionResult",
    "RiskSummary",
    "Scanner",
    "Span",
    "Vault",
    "VaultError",
    "VeilError",
    "Verification",
    "__version__",
    "apply_env",
    "build_vault",
    "catalogue",
    "from_yaml",
    "resolve_spans",
    "score_risk",
    "verify",
]
