"""Domain model for JOBFLOW — the job application pipeline tracker.

Design notes
------------
* **The event log is the source of truth.** An application's *furthest stage*
  is derived from the events that actually happened, not from its current
  status.  An application rejected after an onsite interview genuinely reached
  the onsite rung, and the funnel maths must know that.  Reading it off the
  current status would silently erase every rejection from the funnel.
* **Dates are `datetime.date`, never `datetime`.**  A job hunt is a day-
  resolution activity; carrying a time component invites timezone bugs and
  makes "days since" ambiguous.
* **Salaries are integer minor units.**  Floating point money is a bug waiting
  to happen; the engine adds and compares salaries, so it uses ints.
* **Every analytics call takes an explicit `today`.**  Nothing reads the wall
  clock, so the same vault always produces the same report — which is what
  makes the test suite meaningful and the CLI reproducible.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class JobFlowError(Exception):
    """Base class for every error this package raises deliberately."""


class ValidationError(JobFlowError, ValueError):
    """Raised when input is malformed. Always names the offending value."""


class NotFoundError(JobFlowError, KeyError):
    """Raised when an application id does not exist in the vault."""


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------

#: The ordered funnel.  `applied` is rung 0 and `accepted` is the last rung.
STAGES: Tuple[str, ...] = (
    "applied",
    "screen",
    "interview",
    "onsite",
    "offer",
    "accepted",
)

#: Statuses that end an application.  `accepted` is both a stage and terminal.
TERMINAL: Tuple[str, ...] = ("rejected", "withdrawn")

#: Statuses that mean "in play right now".
ACTIVE: Tuple[str, ...] = ("applied", "screen", "interview", "onsite", "offer")

#: Saved for later, not yet applied to.
PRE: Tuple[str, ...] = ("wishlist",)

#: Everything a status field may hold.
STATUSES: Tuple[str, ...] = PRE + STAGES + TERMINAL

#: Statuses that count as "no longer open".
CLOSED: Tuple[str, ...] = ("rejected", "withdrawn", "accepted")

STAGE_INDEX: Dict[str, int] = {name: i for i, name in enumerate(STAGES)}

STATUS_LABELS: Dict[str, str] = {
    "wishlist": "Wishlist",
    "applied": "Applied",
    "screen": "Recruiter Screen",
    "interview": "Interview",
    "onsite": "Onsite / Final",
    "offer": "Offer",
    "accepted": "Accepted",
    "rejected": "Rejected",
    "withdrawn": "Withdrawn",
}

WORK_MODES: Tuple[str, ...] = ("remote", "hybrid", "onsite", "unspecified")

SOURCES: Tuple[str, ...] = (
    "referral",
    "company_site",
    "linkedin",
    "job_board",
    "recruiter",
    "networking",
    "other",
)

SOURCE_LABELS: Dict[str, str] = {
    "referral": "Referral",
    "company_site": "Company site",
    "linkedin": "LinkedIn",
    "job_board": "Job board",
    "recruiter": "Recruiter",
    "networking": "Networking",
    "other": "Other",
}

#: Event kinds.  A controlled vocabulary keeps the audit trail queryable.
EVENT_KINDS: Tuple[str, ...] = (
    "created",
    "applied",
    "moved",
    "note",
    "followup",
    "interview",
    "closed",
    "edited",
)

INTERVIEW_KINDS: Tuple[str, ...] = (
    "screen",
    "technical",
    "manager",
    "onsite",
    "panel",
    "take_home",
    "final",
    "other",
)

CURRENCY_SYMBOLS: Dict[str, str] = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "PKR": "₨",
    "INR": "₹",
    "AED": "AED ",
    "CAD": "C$",
    "AUD": "A$",
    "SGD": "S$",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# --------------------------------------------------------------------------
# Primitive helpers
# --------------------------------------------------------------------------


def parse_date(value: Any, field_name: str = "date") -> Optional[date]:
    """Parse an ISO ``YYYY-MM-DD`` string (or pass through a ``date``).

    Anything else — including a full timestamp — is rejected loudly.  Silently
    truncating ``2026-09-17T10:00:00Z`` to a date hides a caller bug.
    """

    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        raise ValidationError(
            f"{field_name} must be a date (YYYY-MM-DD), got a datetime: {value!r}"
        )
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a date string, got {type(value).__name__}")
    text = value.strip()
    if not text:
        return None
    if not _DATE_RE.match(text):
        raise ValidationError(f"{field_name} must be ISO YYYY-MM-DD, got {value!r}")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:  # e.g. 2026-02-30
        raise ValidationError(f"{field_name} is not a real calendar date: {value!r}") from exc


def iso(value: Optional[date]) -> Optional[str]:
    """Render a date as ISO text (``None`` passes through)."""

    return value.isoformat() if value is not None else None


def parse_money(value: Any, field_name: str = "amount", currency: str = "USD") -> Optional[int]:
    """Parse a money value into integer minor units (cents).

    Text input is a major-unit amount: ``"120000"``, ``"120,000"``,
    ``"$120,000"`` and ``"120000.50"`` all work.  Rounding is half-up on exact
    thousandths so ``"120000.005"`` cannot drift by a cent.

    Integers are the easy place to get this wrong, so the rule is explicit:
    they are **also** major units (``120000`` -> ``12000000`` minor units) for
    consistency with the text form.  A vault holds values that have already
    been converted and therefore reloads through ``_minor_units_from_vault``,
    never through here — a single loader does not parse ints twice.
    """

    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValidationError(f"{field_name} must be an amount, got a boolean")
    if isinstance(value, int):
        if value < 0:
            raise ValidationError(f"{field_name} cannot be negative: {value}")
        return value * 100
    if isinstance(value, float):
        value = repr(value)
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be an amount, got {type(value).__name__}")
    text = value.strip()
    if not text:
        return None
    # Strip a currency symbol or code if present.
    text = re.sub(r"^[A-Za-z]{3}\s*", "", text)
    text = re.sub(r"^[^\d.\-]+", "", text)
    text = text.replace(",", "").replace("_", "").replace(" ", "")
    if text.startswith("-"):
        raise ValidationError(f"{field_name} cannot be negative: {value!r}")
    if not re.match(r"^\d*(\.\d*)?$", text) or not any(ch.isdigit() for ch in text):
        raise ValidationError(f"{field_name} is not a valid amount: {value!r}")
    whole, _, frac = text.partition(".")
    frac = (frac + "000")[:3]
    minor = int(whole or "0") * 100 + int(frac[:2])
    if frac[2] >= "5":
        minor += 1
    return minor


def format_money(minor: Optional[int], currency: str = "USD", compact: bool = False) -> str:
    """Render minor units for humans.  ``None`` renders as an em dash."""

    if minor is None:
        return "—"
    symbol = CURRENCY_SYMBOLS.get((currency or "USD").upper(), (currency or "").upper() + " ")
    whole = minor // 100
    cents = minor % 100
    if compact and whole >= 1000:
        if whole >= 1_000_000:
            return f"{symbol}{whole / 1_000_000:.1f}M".replace(".0M", "M")
        return f"{symbol}{whole / 1000:.0f}k"
    body = f"{whole:,}"
    if cents:
        body = f"{body}.{cents:02d}"
    return f"{symbol}{body}"


def format_range(
    lo: Optional[int], hi: Optional[int], currency: str = "USD", compact: bool = False
) -> str:
    """Render a salary range, collapsing to a single value when lo == hi."""

    if lo is None and hi is None:
        return "—"
    if lo is not None and hi is not None:
        if lo == hi:
            return format_money(lo, currency, compact)
        return f"{format_money(lo, currency, compact)}–{format_money(hi, currency, compact)}"
    one = lo if lo is not None else hi
    prefix = "up to " if lo is None else "from "
    return prefix + format_money(one, currency, compact)


def slugify(text: str, max_len: int = 48) -> str:
    """ASCII-fold and slugify.  Deterministic: same text, same slug."""

    folded = unicodedata.normalize("NFKD", text or "")
    ascii_only = folded.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return slug[:max_len].strip("-")


def make_id(company: str, role: str, taken: Iterable[str] = ()) -> str:
    """Deterministic id from company + role, suffixed only on collision."""

    base = slugify(f"{company}-{role}") or "application"
    taken_set = set(taken)
    if base not in taken_set:
        return base
    n = 2
    while f"{base}-{n}" in taken_set:
        n += 1
    return f"{base}-{n}"


def clean_text(value: Any, field_name: str, required: bool = False, max_len: int = 200) -> str:
    """Trim a free-text field and enforce required/length rules."""

    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be text, got {type(value).__name__}")
    text = " ".join(value.split())
    if required and not text:
        raise ValidationError(f"{field_name} is required and cannot be empty")
    if len(text) > max_len:
        raise ValidationError(f"{field_name} is too long ({len(text)} > {max_len} characters)")
    return text


def one_of(
    value: Any,
    allowed: Sequence[str],
    field_name: str = "value",
    default: Optional[str] = None,
) -> str:
    """Validate that a value is in a controlled vocabulary.

    ``field_name`` carries a default because the two-argument call is the
    normal one — ``one_of(value, STATUSES)`` — and requiring the name would
    make every ordinary call site a TypeError.
    """

    if value is None or value == "":
        if default is not None:
            return default
        raise ValidationError(f"{field_name} is required (one of: {', '.join(allowed)})")
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if text not in allowed:
        raise ValidationError(
            f"{field_name} must be one of: {', '.join(allowed)} — got {value!r}"
        )
    return text


def parse_priority(value: Any) -> int:
    """Priority is a 1–5 enthusiasm score.  Default 3 (neutral)."""

    if value is None or value == "":
        return 3
    try:
        num = int(value)
    except (TypeError, ValueError):
        raise ValidationError(f"priority must be an integer 1-5, got {value!r}")
    if not 1 <= num <= 5:
        raise ValidationError(f"priority must be between 1 and 5, got {num}")
    return num


# --------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------


@dataclass
class Event:
    """One thing that happened, on one day.  The audit trail of the search."""

    on: str
    kind: str
    note: str = ""
    from_status: Optional[str] = None
    to_status: Optional[str] = None

    def __post_init__(self) -> None:
        parsed = parse_date(self.on, "event.on")
        if parsed is None:
            raise ValidationError("event.on is required")
        self.on = parsed.isoformat()
        self.kind = one_of(self.kind, EVENT_KINDS, "event.kind")
        self.note = clean_text(self.note, "event.note", max_len=400)
        for attr, name in (("from_status", "event.from_status"), ("to_status", "event.to_status")):
            val = getattr(self, attr)
            if val is not None:
                setattr(self, attr, one_of(val, STATUSES, name))

    @property
    def date(self) -> date:
        return date.fromisoformat(self.on)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"on": self.on, "kind": self.kind}
        if self.note:
            out["note"] = self.note
        if self.from_status:
            out["from_status"] = self.from_status
        if self.to_status:
            out["to_status"] = self.to_status
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        if not isinstance(data, dict):
            raise ValidationError(f"event must be an object, got {type(data).__name__}")
        return cls(
            on=data.get("on"),
            kind=data.get("kind"),
            note=data.get("note", ""),
            from_status=data.get("from_status"),
            to_status=data.get("to_status"),
        )


@dataclass
class Interview:
    """A scheduled or completed interview."""

    on: str
    kind: str = "other"
    note: str = ""
    done: bool = False

    def __post_init__(self) -> None:
        parsed = parse_date(self.on, "interview.on")
        if parsed is None:
            raise ValidationError("interview.on is required")
        self.on = parsed.isoformat()
        self.kind = one_of(self.kind, INTERVIEW_KINDS, "interview.kind")
        self.note = clean_text(self.note, "interview.note", max_len=400)
        self.done = bool(self.done)

    @property
    def date(self) -> date:
        return date.fromisoformat(self.on)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"on": self.on, "kind": self.kind, "done": self.done}
        if self.note:
            out["note"] = self.note
        return out

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Interview":
        if not isinstance(data, dict):
            raise ValidationError(f"interview must be an object, got {type(data).__name__}")
        return cls(
            on=data.get("on"),
            kind=data.get("kind", "other"),
            note=data.get("note", ""),
            done=bool(data.get("done", False)),
        )


@dataclass
class Note:
    """A timestamped free-text note."""

    on: str
    text: str

    def __post_init__(self) -> None:
        parsed = parse_date(self.on, "note.on")
        if parsed is None:
            raise ValidationError("note.on is required")
        self.on = parsed.isoformat()
        self.text = clean_text(self.text, "note.text", required=True, max_len=600)

    def to_dict(self) -> Dict[str, Any]:
        return {"on": self.on, "text": self.text}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Note":
        if not isinstance(data, dict):
            raise ValidationError(f"note must be an object, got {type(data).__name__}")
        return cls(on=data.get("on"), text=data.get("text", ""))


@dataclass
class Application:
    """One job application and everything that happened to it."""

    id: str
    company: str
    role: str
    status: str = "wishlist"
    location: str = ""
    work_mode: str = "unspecified"
    source: str = "other"
    url: str = ""
    currency: str = "USD"
    salary_min: Optional[Any] = None
    salary_max: Optional[Any] = None
    priority: int = 3
    #: True only when ``salary_min``/``salary_max`` are already integer minor
    #: units loaded from a vault.  Serialisation sets it; direct construction
    #: (CLI, API, tests) leaves it False so major-unit text is parsed.
    salary_is_minor: bool = False
    created_on: Optional[str] = None
    applied_on: Optional[str] = None
    closed_on: Optional[str] = None
    deadline_on: Optional[str] = None
    next_action: str = ""
    next_action_on: Optional[str] = None
    contact: str = ""
    tags: List[str] = field(default_factory=list)
    events: List[Event] = field(default_factory=list)
    interviews: List[Interview] = field(default_factory=list)
    notes: List[Note] = field(default_factory=list)

    # -- construction ------------------------------------------------------

    def __post_init__(self) -> None:
        self.company = clean_text(self.company, "company", required=True, max_len=120)
        self.role = clean_text(self.role, "role", required=True, max_len=120)
        self.location = clean_text(self.location, "location", max_len=120)
        self.contact = clean_text(self.contact, "contact", max_len=160)
        self.next_action = clean_text(self.next_action, "next_action", max_len=200)
        self.url = clean_text(self.url, "url", max_len=400)
        self.currency = (self.currency or "USD").strip().upper()
        if not re.match(r"^[A-Z]{3}$", self.currency):
            raise ValidationError(f"currency must be a 3-letter code, got {self.currency!r}")
        self.status = one_of(self.status, STATUSES, "status")
        self.work_mode = one_of(self.work_mode, WORK_MODES, "work_mode", default="unspecified")
        self.source = one_of(self.source, SOURCES, "source", default="other")
        self.priority = parse_priority(self.priority)
        self.id = clean_text(self.id, "id", required=True, max_len=80)
        if not re.match(r"^[a-z0-9][a-z0-9-]*$", self.id):
            raise ValidationError(
                f"id must be lowercase letters, digits and hyphens — got {self.id!r}"
            )
        # Salary arrives as text from the CLI and the API, and as integer minor
        # units from a vault.  Normalise once, here, so no other layer has to
        # think about it — and never convert an already-converted value.
        if self.salary_is_minor:
            self.salary_min = self._check_minor(self.salary_min, "salary_min")
            self.salary_max = self._check_minor(self.salary_max, "salary_max")
        else:
            self.salary_min = parse_money(self.salary_min, "salary_min", self.currency)
            self.salary_max = parse_money(self.salary_max, "salary_max", self.currency)
        if self.salary_min is not None and self.salary_max is not None:
            if self.salary_min > self.salary_max:
                raise ValidationError(
                    "salary_min cannot exceed salary_max "
                    f"({format_money(self.salary_min, self.currency)} > "
                    f"{format_money(self.salary_max, self.currency)})"
                )
        self.tags = self._clean_tags(self.tags)
        self.validate()

    @staticmethod
    def _check_minor(value: Any, field_name: str) -> Optional[int]:
        """Validate a value that is already integer minor units."""

        if value is None or value == "":
            return None
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(
                f"{field_name} must be an integer number of minor units when "
                f"loaded from a vault, got {type(value).__name__}: {value!r}"
            )
        if value < 0:
            raise ValidationError(f"{field_name} cannot be negative: {value}")
        return value

    @staticmethod
    def _clean_tags(tags: Any) -> List[str]:
        if tags is None:
            return []
        if isinstance(tags, str):
            tags = [t for t in re.split(r"[,\s]+", tags) if t]
        if not isinstance(tags, (list, tuple)):
            raise ValidationError(f"tags must be a list, got {type(tags).__name__}")
        out: List[str] = []
        for tag in tags:
            clean = slugify(str(tag), max_len=24)
            if clean and clean not in out:
                out.append(clean)
        return sorted(out)

    # -- validation --------------------------------------------------------

    def validate(self) -> "Application":
        """Deep-validate dates/statuses.  Called after any mutation."""

        for attr in ("created_on", "applied_on", "closed_on", "deadline_on", "next_action_on"):
            parsed = parse_date(getattr(self, attr), attr)
            setattr(self, attr, iso(parsed))

        if self.status in STAGES[1:] and not self.applied_on:
            # Derive the apply date from the event log if the field is empty.
            applied_events = [e for e in self.events if e.kind == "applied"]
            if applied_events:
                self.applied_on = min(e.on for e in applied_events)
            elif self.status in ("screen", "interview", "onsite", "offer", "accepted"):
                raise ValidationError(
                    f"{self.id}: status is {self.status!r} but the application has no "
                    "applied date — log the application first (or pass applied_on)"
                )

        if self.status in ("rejected", "withdrawn", "accepted") and not self.closed_on:
            closed = [e for e in self.events if e.kind == "closed" or e.to_status in CLOSED]
            if closed:
                self.closed_on = max(e.on for e in closed)
            else:
                self.closed_on = self.applied_on or self.created_on

        if self.applied_on and self.created_on and self.applied_on < self.created_on:
            raise ValidationError(
                f"{self.id}: applied_on ({self.applied_on}) is before created_on ({self.created_on})"
            )
        if self.deadline_on and self.applied_on and self.deadline_on < self.applied_on:
            raise ValidationError(
                f"{self.id}: deadline_on ({self.deadline_on}) is before applied_on ({self.applied_on})"
            )
        return self

    # -- derived state -----------------------------------------------------

    @property
    def label(self) -> str:
        """Human label used in every report line."""

        return f"{self.company} — {self.role}"

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE

    @property
    def is_closed(self) -> bool:
        return self.status in CLOSED

    @property
    def was_applied(self) -> bool:
        """True once the application was actually submitted."""

        return bool(self.applied_on) or any(e.kind == "applied" for e in self.events)

    def furthest_stage(self) -> Optional[str]:
        """The deepest funnel rung this application genuinely reached.

        Derived from the event log so that a rejection *after* an onsite still
        counts as having reached the onsite rung.  Returns ``None`` when the
        application was never submitted.
        """

        best: Optional[str] = None
        best_idx = -1

        if self.applied_on:
            best, best_idx = "applied", 0

        for event in self.events:
            for candidate in (event.to_status, event.from_status):
                if candidate in STAGE_INDEX and STAGE_INDEX[candidate] > best_idx:
                    best, best_idx = candidate, STAGE_INDEX[candidate]

        # A current status that is a stage is itself evidence of arrival.
        if self.status in STAGE_INDEX and STAGE_INDEX[self.status] > best_idx:
            best, best_idx = self.status, STAGE_INDEX[self.status]

        return best

    def reached_index(self) -> int:
        """Numeric rung of :meth:`furthest_stage` (-1 when never applied)."""

        stage = self.furthest_stage()
        return STAGE_INDEX[stage] if stage else -1

    def has_response(self) -> bool:
        """Did the employer ever answer?

        A rejection **is** a response — the employer read the application and
        replied.  Only silence counts as no response.  Counting rejections as
        non-responses is the most common way a job-hunt dashboard lies.
        """

        if self.reached_index() > 0:
            return True
        return any(
            e.kind in ("followup", "interview", "moved", "closed") for e in self.events
        )

    def stage_entered_on(self) -> Optional[date]:
        """When the current stage began."""

        markers: List[str] = []
        if self.status == "applied" and self.applied_on:
            markers.append(self.applied_on)
        for event in self.events:
            if event.to_status == self.status:
                markers.append(event.on)
        if markers:
            return date.fromisoformat(max(markers))
        if self.applied_on:
            return date.fromisoformat(self.applied_on)
        if self.created_on:
            return date.fromisoformat(self.created_on)
        return None

    def days_in_stage(self, today: date) -> int:
        """Whole days spent in the current status (0 if unknown/future)."""

        entered = self.stage_entered_on()
        if entered is None:
            return 0
        return max(0, (today - entered).days)

    def age_days(self, today: date) -> int:
        """Days since the application was submitted (0 if not yet applied)."""

        if not self.applied_on:
            return 0
        return max(0, (today - date.fromisoformat(self.applied_on)).days)

    def last_activity_on(self) -> Optional[date]:
        """Most recent date anything at all happened."""

        stamps: List[str] = []
        stamps.extend(e.on for e in self.events)
        stamps.extend(i.on for i in self.interviews)
        stamps.extend(n.on for n in self.notes)
        for attr in ("applied_on", "created_on"):
            value = getattr(self, attr)
            if value:
                stamps.append(value)
        if not stamps:
            return None
        return date.fromisoformat(max(stamps))

    def days_since_activity(self, today: date) -> int:
        last = self.last_activity_on()
        if last is None:
            return 0
        return max(0, (today - last).days)

    def upcoming_interviews(self, today: date) -> List[Interview]:
        return sorted(
            (i for i in self.interviews if not i.done and i.date >= today),
            key=lambda i: (i.on, i.kind),
        )

    def last_followup_on(self) -> Optional[date]:
        stamps = [e.on for e in self.events if e.kind == "followup"]
        return date.fromisoformat(max(stamps)) if stamps else None

    def followup_count(self) -> int:
        return sum(1 for e in self.events if e.kind == "followup")

    # -- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "company": self.company,
            "role": self.role,
            "status": self.status,
            "location": self.location,
            "work_mode": self.work_mode,
            "source": self.source,
            "url": self.url,
            "currency": self.currency,
            "salary_min": self.salary_min,
            "salary_max": self.salary_max,
            "priority": self.priority,
            "created_on": self.created_on,
            "applied_on": self.applied_on,
            "closed_on": self.closed_on,
            "deadline_on": self.deadline_on,
            "next_action": self.next_action,
            "next_action_on": self.next_action_on,
            "contact": self.contact,
            "tags": list(self.tags),
            "events": [e.to_dict() for e in self.events],
            "interviews": [i.to_dict() for i in self.interviews],
            "notes": [n.to_dict() for n in self.notes],
        }
        return out

    @classmethod
    def from_dict(cls, data: Any) -> "Application":
        if not isinstance(data, dict):
            raise ValidationError(f"application must be an object, got {type(data).__name__}")
        events = [Event.from_dict(e) for e in data.get("events") or []]
        events.sort(key=lambda e: (e.on, e.kind))
        interviews = sorted(
            (Interview.from_dict(i) for i in data.get("interviews") or []),
            key=lambda i: (i.on, i.kind),
        )
        notes = sorted((Note.from_dict(n) for n in data.get("notes") or []), key=lambda n: n.on)
        return cls(
            id=data.get("id"),
            company=data.get("company"),
            role=data.get("role"),
            status=data.get("status", "wishlist"),
            location=data.get("location", ""),
            work_mode=data.get("work_mode", "unspecified"),
            source=data.get("source", "other"),
            url=data.get("url", ""),
            currency=data.get("currency", "USD"),
            salary_min=data.get("salary_min"),
            salary_max=data.get("salary_max"),
            # Vault values are already integer minor units — never re-convert.
            salary_is_minor=True,
            priority=data.get("priority", 3),
            created_on=data.get("created_on"),
            applied_on=data.get("applied_on"),
            closed_on=data.get("closed_on"),
            deadline_on=data.get("deadline_on"),
            next_action=data.get("next_action", ""),
            next_action_on=data.get("next_action_on"),
            contact=data.get("contact", ""),
            tags=data.get("tags") or [],
            events=events,
            interviews=interviews,
            notes=notes,
        )
