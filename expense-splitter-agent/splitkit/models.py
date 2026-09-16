"""Domain models for SplitKit: members, expenses, groups.

The models are deliberately plain — dataclasses with explicit
``to_dict``/``from_dict`` — so the same group file round-trips through JSON
byte-for-byte. That property is what lets the CLI, the REST API, and the
browser demo agree on every number, and it is asserted in the test suite.

Ordering is meaningful: members and expenses are held in insertion order, and
every downstream computation (balances, settle-up, reports) iterates that
order or sorts with an explicit tie-break. Nothing depends on dict hashing.
"""

from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from .errors import UnknownExpenseError, UnknownMemberError, ValidationError
from .money import exponent_for, parse_amount

SCHEMA_VERSION = 1

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

#: Categories used for the spend breakdown. Free-text categories are allowed
#: via ``--category``; these are the ones that get icons and sensible ordering.
KNOWN_CATEGORIES = (
    "food", "groceries", "transport", "lodging", "utilities", "rent",
    "entertainment", "shopping", "health", "travel", "other",
)

CATEGORY_ICONS = {
    "food": "\U0001f37d", "groceries": "\U0001f6d2", "transport": "\U0001f697",
    "lodging": "\U0001f3e8", "utilities": "\U0001f4a1", "rent": "\U0001f3e0",
    "entertainment": "\U0001f3ac", "shopping": "\U0001f6cd", "health": "\u2695",
    "travel": "\u2708", "other": "\U0001f4cc",
}


def slugify(text: str) -> str:
    """Turn a display name into a stable member id."""
    s = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "member"


def is_valid_slug(text: str) -> bool:
    return bool(_SLUG_RE.match(text or ""))


def today_iso() -> str:
    return _dt.date.today().isoformat()


def parse_date(text: Optional[str], *, field_name: str = "date") -> str:
    """Validate an ISO date, defaulting to today.

    Dates are stored as ``YYYY-MM-DD`` strings rather than ``date`` objects so
    a group file stays human-readable and diff-friendly.
    """
    if text is None or text == "":
        return today_iso()
    if not isinstance(text, str):
        raise ValidationError(f"{field_name} must be a YYYY-MM-DD string")
    try:
        return _dt.date.fromisoformat(text.strip()).isoformat()
    except ValueError as exc:
        raise ValidationError(f"{field_name} {text!r} is not a valid ISO date: {exc}") from exc


def normalise_currency(code: str) -> str:
    if not isinstance(code, str):
        raise ValidationError("currency must be a 3-letter ISO code")
    c = code.strip().upper()
    if not _CURRENCY_RE.match(c):
        raise ValidationError(f"currency {code!r} must be a 3-letter ISO code like USD or PKR")
    return c


@dataclass
class Member:
    """One participant in a group."""

    id: str
    name: str
    note: str = ""

    def __post_init__(self) -> None:
        if not is_valid_slug(self.id):
            raise ValidationError(
                f"member id {self.id!r} must be lowercase letters, digits, '-' or '_'"
            )
        self.name = (self.name or self.id).strip()
        if not self.name:
            raise ValidationError(f"member {self.id!r} needs a non-empty name")

    @property
    def display(self) -> str:
        return self.name

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"id": self.id, "name": self.name}
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Member":
        if not isinstance(raw, dict):
            raise ValidationError(f"member entry must be an object, got {type(raw).__name__}")
        ident = raw.get("id") or raw.get("slug")
        if not ident:
            name = raw.get("name")
            if not name:
                raise ValidationError("member entry needs an 'id' or a 'name'")
            ident = slugify(str(name))
        return cls(
            id=str(ident).strip().lower(),
            name=str(raw.get("name") or ident),
            note=str(raw.get("note") or ""),
        )


@dataclass
class Expense:
    """One shared cost, plus the rule that says who owes what share of it."""

    id: str
    description: str
    amount: int  # minor units
    paid_by: str
    split: Dict[str, Any] = field(default_factory=lambda: {"mode": "equal"})
    date: str = ""
    category: str = "other"
    note: str = ""

    def __post_init__(self) -> None:
        self.description = (self.description or "").strip()
        if not self.description:
            raise ValidationError(f"expense {self.id!r} needs a description")
        if not isinstance(self.amount, int) or isinstance(self.amount, bool):
            raise ValidationError(
                f"expense {self.id!r} amount must be integer minor units, got {type(self.amount).__name__}"
            )
        if not is_valid_slug(self.paid_by):
            raise ValidationError(f"expense {self.id!r} has an invalid payer id {self.paid_by!r}")
        self.date = parse_date(self.date or None)
        self.category = (self.category or "other").strip().lower() or "other"
        if not isinstance(self.split, dict):
            raise ValidationError(f"expense {self.id!r} split must be an object")

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "amount": self.amount,
            "paid_by": self.paid_by,
            "split": self.split,
            "date": self.date,
            "category": self.category,
        }
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], *, currency: str = "USD") -> "Expense":
        if not isinstance(raw, dict):
            raise ValidationError(f"expense entry must be an object, got {type(raw).__name__}")
        ident = raw.get("id")
        if not ident:
            raise ValidationError("expense entry needs an 'id'")
        exp = exponent_for(currency)
        amount = raw.get("amount")
        if isinstance(amount, str):
            amount = parse_amount(amount, exp)
        elif amount is None:
            amount = raw.get("amount_minor")
        elif isinstance(amount, float):
            raise ValidationError(
                f"expense {ident!r}: amount must not be a float — send \"12.50\" or minor units"
            )
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValidationError(f"expense {ident!r} needs an integer amount")
        return cls(
            id=str(ident),
            description=str(raw.get("description") or raw.get("label") or "").strip(),
            amount=amount,
            paid_by=str(raw.get("paid_by") or raw.get("payer") or "").strip().lower(),
            split=raw.get("split") or {"mode": "equal"},
            date=str(raw.get("date") or ""),
            category=str(raw.get("category") or "other"),
            note=str(raw.get("note") or ""),
        )


@dataclass
class Settlement:
    """A recorded repayment, so settled debts leave the balance report."""

    from_member: str
    to_member: str
    amount: int
    date: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if not is_valid_slug(self.from_member):
            raise ValidationError(f"settlement has an invalid payer {self.from_member!r}")
        if not is_valid_slug(self.to_member):
            raise ValidationError(f"settlement has an invalid payee {self.to_member!r}")
        if self.from_member == self.to_member:
            raise ValidationError("a settlement cannot be from someone to themselves")
        if not isinstance(self.amount, int) or isinstance(self.amount, bool) or self.amount <= 0:
            raise ValidationError("a settlement amount must be a positive integer of minor units")
        self.date = parse_date(self.date or None)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "from": self.from_member,
            "to": self.to_member,
            "amount": self.amount,
            "date": self.date,
        }
        if self.note:
            d["note"] = self.note
        return d

    @classmethod
    def from_dict(cls, raw: Dict[str, Any], *, currency: str = "USD") -> "Settlement":
        if not isinstance(raw, dict):
            raise ValidationError("settlement entry must be an object")
        amount = raw.get("amount")
        if isinstance(amount, str):
            amount = parse_amount(amount, exponent_for(currency))
        if not isinstance(amount, int) or isinstance(amount, bool):
            raise ValidationError("settlement entry needs an integer amount")
        return cls(
            from_member=str(raw.get("from") or raw.get("from_member") or "").strip().lower(),
            to_member=str(raw.get("to") or raw.get("to_member") or "").strip().lower(),
            amount=amount,
            date=str(raw.get("date") or ""),
            note=str(raw.get("note") or ""),
        )


@dataclass
class Group:
    """A set of people, their shared costs, and the repayments made so far."""

    id: str
    name: str
    currency: str = "USD"
    members: List[Member] = field(default_factory=list)
    expenses: List[Expense] = field(default_factory=list)
    settlements: List[Settlement] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.currency = normalise_currency(self.currency)
        if not is_valid_slug(self.id):
            raise ValidationError(f"group id {self.id!r} must be a slug")
        self.name = (self.name or self.id).strip() or self.id
        seen: set[str] = set()
        for m in self.members:
            if m.id in seen:
                raise ValidationError(f"duplicate member id {m.id!r}")
            seen.add(m.id)
        exp_ids: set[str] = set()
        for e in self.expenses:
            if e.id in exp_ids:
                raise ValidationError(f"duplicate expense id {e.id!r}")
            exp_ids.add(e.id)
            if e.paid_by not in seen:
                raise UnknownMemberError(
                    f"expense {e.id!r} is paid by {e.paid_by!r}, who is not in the group"
                )

    # ---- member management -------------------------------------------------

    @property
    def member_ids(self) -> List[str]:
        return [m.id for m in self.members]

    def member(self, member_id: str) -> Member:
        for m in self.members:
            if m.id == member_id:
                return m
        raise UnknownMemberError(f"no member {member_id!r} in group {self.id!r}")

    def has_member(self, member_id: str) -> bool:
        return any(m.id == member_id for m in self.members)

    def name_of(self, member_id: str) -> str:
        return self.member(member_id).name

    def add_member(self, name: str, *, member_id: Optional[str] = None, note: str = "") -> Member:
        ident = (member_id or slugify(name)).strip().lower()
        if self.has_member(ident):
            raise ValidationError(f"member {ident!r} already exists in {self.id!r}")
        m = Member(id=ident, name=name, note=note)
        self.members.append(m)
        return m

    def remove_member(self, member_id: str, *, force: bool = False) -> None:
        """Remove a member. Refuses while they still appear in any expense.

        ``force=True`` removes them *and* every expense/settlement that
        references them — destructive, so the CLI requires an explicit flag.
        """
        self.member(member_id)  # raises when absent
        used = [e.id for e in self.expenses if e.paid_by == member_id or _expense_touches(e, member_id)]
        settled = [
            s for s in self.settlements
            if s.from_member == member_id or s.to_member == member_id
        ]
        if (used or settled) and not force:
            detail = []
            if used:
                detail.append(f"{len(used)} expense(s): {', '.join(used[:4])}")
            if settled:
                detail.append(f"{len(settled)} recorded settlement(s)")
            raise ValidationError(
                f"member {member_id!r} is still referenced by " + " and ".join(detail)
                + " — remove those first, or pass force to delete them along with the member"
            )
        if force:
            self.expenses = [
                e for e in self.expenses
                if e.paid_by != member_id and not _expense_touches(e, member_id)
            ]
            self.settlements = [
                s for s in self.settlements
                if s.from_member != member_id and s.to_member != member_id
            ]
        self.members = [m for m in self.members if m.id != member_id]

    # ---- expense management ------------------------------------------------

    def next_expense_id(self) -> str:
        used = {e.id for e in self.expenses}
        n = len(self.expenses) + 1
        while f"e{n}" in used:
            n += 1
        return f"e{n}"

    def expense(self, expense_id: str) -> Expense:
        for e in self.expenses:
            if e.id == expense_id:
                return e
        raise UnknownExpenseError(f"no expense {expense_id!r} in group {self.id!r}")

    def add_expense(
        self,
        description: str,
        amount: int,
        paid_by: str,
        split: Optional[Dict[str, Any]] = None,
        *,
        expense_id: Optional[str] = None,
        date: Optional[str] = None,
        category: str = "other",
        note: str = "",
    ) -> Expense:
        if not self.has_member(paid_by):
            raise UnknownMemberError(
                f"payer {paid_by!r} is not in group {self.id!r} (members: {', '.join(self.member_ids)})"
            )
        eid = expense_id or self.next_expense_id()
        if any(e.id == eid for e in self.expenses):
            raise ValidationError(f"expense id {eid!r} already exists")
        e = Expense(
            id=eid,
            description=description,
            amount=amount,
            paid_by=paid_by,
            split=split if split is not None else {"mode": "equal"},
            date=date or "",
            category=category,
            note=note,
        )
        self.expenses.append(e)
        return e

    def remove_expense(self, expense_id: str) -> Expense:
        e = self.expense(expense_id)
        self.expenses = [x for x in self.expenses if x.id != expense_id]
        return e

    def add_settlement(
        self,
        from_member: str,
        to_member: str,
        amount: int,
        *,
        date: Optional[str] = None,
        note: str = "",
    ) -> Settlement:
        for who in (from_member, to_member):
            if not self.has_member(who):
                raise UnknownMemberError(f"{who!r} is not in group {self.id!r}")
        s = Settlement(
            from_member=from_member, to_member=to_member, amount=amount,
            date=date or "", note=note,
        )
        self.settlements.append(s)
        return s

    # ---- totals ------------------------------------------------------------

    @property
    def total_minor(self) -> int:
        return sum(e.amount for e in self.expenses)

    def expenses_touching(self, member_id: str) -> List[Expense]:
        return [
            e for e in self.expenses
            if e.paid_by == member_id or _expense_touches(e, member_id)
        ]

    # ---- serialisation -----------------------------------------------------

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "id": self.id,
            "name": self.name,
            "currency": self.currency,
            "members": [m.to_dict() for m in self.members],
            "expenses": [e.to_dict() for e in self.expenses],
            "settlements": [s.to_dict() for s in self.settlements],
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "Group":
        if not isinstance(raw, dict):
            raise ValidationError(f"group file must contain a JSON object, got {type(raw).__name__}")
        currency = raw.get("currency") or "USD"
        currency = normalise_currency(str(currency))
        members = [Member.from_dict(m) for m in (raw.get("members") or [])]
        if not members:
            raise ValidationError("group needs at least one member")
        expenses = [
            Expense.from_dict(e, currency=currency) for e in (raw.get("expenses") or [])
        ]
        settlements = [
            Settlement.from_dict(s, currency=currency) for s in (raw.get("settlements") or [])
        ]
        return cls(
            id=str(raw.get("id") or "group").strip().lower(),
            name=str(raw.get("name") or raw.get("id") or "Group"),
            currency=currency,
            members=members,
            expenses=expenses,
            settlements=settlements,
            schema_version=int(raw.get("schema_version") or SCHEMA_VERSION),
        )


def _expense_touches(expense: Expense, member_id: str) -> bool:
    """True when a member appears anywhere inside the expense's split rule."""
    spec = expense.split or {}
    mode = str(spec.get("mode") or "equal").lower()
    if mode == "equal" or mode == "adjustment":
        participants = spec.get("participants")
        if participants:
            return member_id in [str(p).lower() for p in participants]
        return True  # open to all members
    for key in ("amounts", "shares", "percents", "adjustments"):
        mapping = spec.get(key)
        if isinstance(mapping, dict) and member_id in {str(k).lower() for k in mapping}:
            return True
    items = spec.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            parts = item.get("participants")
            if parts and member_id in [str(p).lower() for p in parts]:
                return True
            if not parts:
                return True  # item shared by everyone
    return False


__all__ = [
    "SCHEMA_VERSION",
    "KNOWN_CATEGORIES",
    "CATEGORY_ICONS",
    "Member",
    "Expense",
    "Settlement",
    "Group",
    "slugify",
    "is_valid_slug",
    "parse_date",
    "today_iso",
    "normalise_currency",
]
