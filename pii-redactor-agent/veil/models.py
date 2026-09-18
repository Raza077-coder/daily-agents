"""Data models and the entity catalogue.

The engine is plain-data in, plain-data out. Every stage produces dataclasses
that serialise to JSON with no schema layer on top, which is what lets the CLI,
the REST API and the browser demo describe an identical result in an identical
shape.

Nothing here reads the clock or a random source: two runs over the same input
produce byte-identical results, and that is asserted by the test suite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Entity catalogue
# ---------------------------------------------------------------------------

#: Risk weight per entity, used for the sensitivity score. Higher means the
#: value is more damaging if it leaks. Credentials and payment identifiers lead,
#: network addresses trail.
RISK_WEIGHTS: Dict[str, int] = {
    "SECRET": 10,
    "CREDIT_CARD": 9,
    "SSN": 9,
    "IBAN": 8,
    "NATIONAL_ID": 8,
    "DATE_OF_BIRTH": 5,
    "EMAIL": 4,
    "PHONE": 4,
    "PERSON": 3,
    "MAC": 3,
    "IPV4": 2,
    "IPV6": 2,
    "URL": 2,
}

ENTITY_LABELS: Dict[str, str] = {
    "EMAIL": "Email address",
    "PHONE": "Phone number",
    "CREDIT_CARD": "Payment card number",
    "IBAN": "Bank account (IBAN)",
    "SSN": "US social security number",
    "NATIONAL_ID": "National identity number",
    "IPV4": "IPv4 address",
    "IPV6": "IPv6 address",
    "MAC": "MAC address",
    "URL": "URL",
    "SECRET": "Credential or API key",
    "DATE_OF_BIRTH": "Date of birth",
    "PERSON": "Person name",
}

#: What makes a candidate real. The engine owns this text so the reference table
#: in the docs and the web HUD is generated from the code and cannot drift.
ENTITY_VALIDATORS: Dict[str, str] = {
    "EMAIL": "Structural check: no leading or trailing dot in the local part, no consecutive dots, TLD 2-24 letters.",
    "PHONE": "7-15 digits, and either a leading + country code, two or more separators, or a nearby context word (phone/tel/mobile/cell/whatsapp/call/fax).",
    "CREDIT_CARD": "13-19 digits passing the Luhn checksum. A 16-digit run that fails Luhn is not reported.",
    "IBAN": "ISO 13616 length (15-34) and ISO 7064 mod-97 remainder of exactly 1.",
    "SSN": "US SSA issuance rules: area not 000/666/900-999, group non-zero, serial non-zero. Separators required.",
    "NATIONAL_ID": "Fixed national format; the bundled pattern is the Pakistani CNIC (#####-#######-#).",
    "IPV4": "Four octets, each 0-255, not part of a longer dotted run.",
    "IPV6": "Two or more hextet groups, including :: compression.",
    "MAC": "Six hex octets separated by : or -.",
    "URL": "http/https scheme with a host that contains a dot.",
    "SECRET": "Known provider prefixes (AWS, GitHub, Slack, Stripe, JWT, PEM private key), Bearer tokens, or a key/value assignment such as api_key=... where the *value* is flagged, not the key.",
    "DATE_OF_BIRTH": "A date literal appearing within 24 characters of an explicit birth-context word (dob, born, date of birth, birthdate).",
    "PERSON": "Opt-in only. Honorific pattern (Mr/Mrs/Ms/Dr/Prof) or a whole-word match against the built-in first-name dictionary plus any names supplied in the policy.",
}

#: Entities that stay off until the policy turns them on, because their detection
#: is heuristic rather than validated and would otherwise flag ordinary prose.
OPT_IN_ENTITIES: Tuple[str, ...] = ("PERSON",)

#: Every entity the engine knows about, in catalogue order.
ALL_ENTITIES: Tuple[str, ...] = tuple(ENTITY_LABELS.keys())

#: The actions an entity can map to. Declared here rather than in `policy` or
#: `actions` because both need them and neither should import the other.
ACTION_KEEP = "keep"
ACTIONS: Tuple[str, ...] = ("mask", "redact", "hash", "tokenize", "remove", ACTION_KEEP)

#: Default character used to obscure a masked value.
MASK_CHAR = "\u2022"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """One detected occurrence, as a half-open range into the source text."""

    start: int
    end: int
    entity: str
    value: str
    detector: str
    confidence: float
    note: str = ""

    @property
    def length(self) -> int:
        return self.end - self.start

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "entity": self.entity,
            "value": self.value,
            "detector": self.detector,
            "confidence": round(self.confidence, 3),
            "note": self.note,
        }


@dataclass
class Finding:
    """A resolved finding: one distinct value, its action, and where it occurred.

    Findings are grouped by value rather than by occurrence, so a report says
    "this email appeared 4 times" instead of listing four near-identical rows.
    """

    entity: str
    value: str
    action: str
    replacement: str
    count: int
    offsets: List[Tuple[int, int]] = field(default_factory=list)
    detector: str = ""
    confidence: float = 0.0
    note: str = ""
    preserved: bool = False

    @property
    def risk_weight(self) -> int:
        return RISK_WEIGHTS.get(self.entity, 1)

    def to_dict(self) -> dict:
        return {
            "entity": self.entity,
            "value": self.value,
            "action": self.action,
            "replacement": self.replacement,
            "count": self.count,
            "offsets": [list(o) for o in self.offsets],
            "detector": self.detector,
            "confidence": round(self.confidence, 3),
            "note": self.note,
            "preserved": self.preserved,
            "risk_weight": self.risk_weight,
        }

    def to_public_dict(self) -> dict:
        """Same shape with the raw value withheld, for logs and shared reports."""
        data = self.to_dict()
        data["value"] = "[withheld]"
        return data


@dataclass
class RiskSummary:
    """Weighted sensitivity of the scanned text."""

    score: int
    level: str
    distinct_findings: int
    total_occurrences: int
    by_entity: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "level": self.level,
            "distinct_findings": self.distinct_findings,
            "total_occurrences": self.total_occurrences,
            "by_entity": dict(self.by_entity),
        }


@dataclass
class Verification:
    """Result of re-scanning the redacted output for anything that survived."""

    status: str
    checked_entities: List[str] = field(default_factory=list)
    residuals: List[dict] = field(default_factory=list)
    preserved: List[dict] = field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.status == "CLEAN"

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "clean": self.clean,
            "checked_entities": list(self.checked_entities),
            "residuals": list(self.residuals),
            "preserved": list(self.preserved),
        }


@dataclass
class RedactionResult:
    """Everything the engine produces for one document."""

    text: str
    findings: List[Finding] = field(default_factory=list)
    risk: RiskSummary | None = None
    verification: Verification | None = None
    policy_name: str = "default"
    token_map: Dict[str, str] = field(default_factory=dict)
    #: True only when ``text`` is the transformed output. Scan-only results keep
    #: the original text in ``text``, so this flag is what stops a report from
    #: echoing a document back under a key called "redacted".
    is_redacted: bool = False

    @property
    def applied(self) -> List[Finding]:
        return [f for f in self.findings if not f.preserved]

    def to_dict(self, include_values: bool = True) -> dict:
        emit = (lambda f: f.to_dict()) if include_values else (lambda f: f.to_public_dict())
        return {
            "policy": self.policy_name,
            "redacted": self.text if self.is_redacted else None,
            "redacted_emitted": self.is_redacted,
            "findings": [emit(f) for f in self.findings],
            "applied_count": len(self.applied),
            "risk": self.risk.to_dict() if self.risk else None,
            "verification": self.verification.to_dict() if self.verification else None,
            "token_map": dict(self.token_map),
        }


def catalogue() -> List[dict]:
    """The entity reference table, used by the API and the generated demo data."""
    return [
        {
            "entity": name,
            "label": ENTITY_LABELS[name],
            "validator": ENTITY_VALIDATORS[name],
            "risk_weight": RISK_WEIGHTS[name],
            "opt_in": name in OPT_IN_ENTITIES,
        }
        for name in ALL_ENTITIES
    ]
