"""Ledger: turn a list of expenses into net positions per member.

The whole point of a shared-expense ledger is one invariant:

    **every balance sums to exactly zero**

because each expense is both a credit to the payer and a set of debits to the
beneficiaries. If that sum is ever non-zero, money has been created or
destroyed — so :func:`balances` computes it and raises rather than returning a
report that quietly does not add up.

Follows the standard convention: a **positive** balance means the group owes
that person (they paid more than their share); a **negative** balance means
they owe the group.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .errors import SettleError, SplitError, ValidationError
from .models import CATEGORY_ICONS, Expense, Group, Member
from .money import allocate, exponent_for, format_amount
from .splits import describe, resolve


@dataclass
class Share:
    """One member's slice of one expense."""

    member_id: str
    amount: int

    def to_dict(self) -> Dict[str, Any]:
        return {"member": self.member_id, "amount": self.amount}


@dataclass
class ExpenseBreakdown:
    """A fully resolved expense: who paid, and who owes what slice."""

    expense: Expense
    shares: Dict[str, int]

    @property
    def id(self) -> str:
        return self.expense.id

    @property
    def total(self) -> int:
        return self.expense.amount

    def to_dict(self, currency: str = "USD") -> Dict[str, Any]:
        exp = exponent_for(currency)
        return {
            "id": self.expense.id,
            "description": self.expense.description,
            "amount": self.expense.amount,
            "amount_display": format_amount(self.expense.amount, exp),
            "paid_by": self.expense.paid_by,
            "date": self.expense.date,
            "category": self.expense.category,
            "split": self.expense.split,
            "split_explained": describe(self.expense.split, lambda m: m, currency),
            "shares": dict(self.shares),
        }


def breakdowns(group: Group) -> List[ExpenseBreakdown]:
    """Resolve every expense in the group into concrete per-member shares."""
    out: List[ExpenseBreakdown] = []
    for e in group.expenses:
        shares = resolve(e.split, e.amount, group.member_ids, currency=group.currency)
        total = sum(shares.values())
        if total != e.amount:  # pragma: no cover - resolve() guarantees this
            raise SplitError(
                f"expense {e.id!r} resolved to {total} but is recorded as {e.amount}"
            )
        out.append(ExpenseBreakdown(expense=e, shares=shares))
    return out


def paid_totals(group: Group) -> Dict[str, int]:
    """How much cash each member actually fronted."""
    totals = {m.id: 0 for m in group.members}
    for e in group.expenses:
        totals[e.paid_by] = totals.get(e.paid_by, 0) + e.amount
    return totals


def owed_totals(group: Group) -> Dict[str, int]:
    """How much of the spend each member is responsible for."""
    totals = {m.id: 0 for m in group.members}
    for bd in breakdowns(group):
        for who, amt in bd.shares.items():
            totals[who] = totals.get(who, 0) + amt
    return totals


def settled_totals(group: Group) -> Dict[str, int]:
    """Net effect of recorded repayments: payers gain, receivers lose."""
    net = {m.id: 0 for m in group.members}
    for s in group.settlements:
        net[s.from_member] = net.get(s.from_member, 0) + s.amount
        net[s.to_member] = net.get(s.to_member, 0) - s.amount
    return net


def balances(group: Group, *, check_invariant: bool = True) -> Dict[str, int]:
    """Net position per member in minor units.

    Positive = the group owes them. Negative = they owe the group.
    Raises :class:`SettleError` if the balances do not sum to zero, which would
    mean the ledger is corrupt (a split that lost or invented a minor unit).
    """
    paid = paid_totals(group)
    owed = owed_totals(group)
    settled = settled_totals(group)
    net = {
        m.id: paid.get(m.id, 0) + settled.get(m.id, 0) - owed.get(m.id, 0)
        for m in group.members
    }
    if check_invariant:
        total = sum(net.values())
        if total != 0:
            exp = exponent_for(group.currency)
            raise SettleError(
                f"ledger for group {group.id!r} does not balance: balances sum to "
                f"{format_amount(total, exp, plus=True)} instead of 0. This means at "
                "least one expense split lost or invented money — check the expense "
                "amounts against their splits."
            )
    return net


def creditors_and_debtors(group: Group) -> tuple[List[tuple[str, int]], List[tuple[str, int]]]:
    """Split balances into ``(creditors, debtors)``, each sorted largest-first.

    Ties break alphabetically so the settle-up plan is deterministic run to run.
    """
    net = balances(group)
    creditors = sorted(
        ((m, v) for m, v in net.items() if v > 0), key=lambda kv: (-kv[1], kv[0])
    )
    debtors = sorted(
        ((m, v) for m, v in net.items() if v < 0), key=lambda kv: (kv[1], kv[0])
    )
    return creditors, debtors


def category_totals(group: Group) -> Dict[str, int]:
    """Total spend per category, largest first."""
    totals: Dict[str, int] = {}
    for e in group.expenses:
        totals[e.category] = totals.get(e.category, 0) + e.amount
    return dict(sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])))


def top_expenses(group: Group, limit: int = 5) -> List[Expense]:
    """The largest expenses, biggest first (ties by id for determinism)."""
    return sorted(group.expenses, key=lambda e: (-e.amount, e.id))[: max(limit, 0)]


def member_spend_matrix(group: Group) -> Dict[str, Dict[str, int]]:
    """Per-member responsibility broken down by category.

    Answers "where did my money actually go?" rather than just what was paid.
    """
    matrix: Dict[str, Dict[str, int]] = {m.id: {} for m in group.members}
    for bd in breakdowns(group):
        cat = bd.expense.category
        for who, amt in bd.shares.items():
            bucket = matrix.setdefault(who, {})
            bucket[cat] = bucket.get(cat, 0) + amt
    return {
        who: dict(sorted(cats.items(), key=lambda kv: (-kv[1], kv[0])))
        for who, cats in matrix.items()
    }


def health(group: Group) -> Dict[str, Any]:
    """Summary numbers used by the report and the browser HUD."""
    net = balances(group)
    creditors, debtors = creditors_and_debtors(group)
    exp = exponent_for(group.currency)
    settled_amount = sum(s.amount for s in group.settlements)
    return {
        "currency": group.currency,
        "member_count": len(group.members),
        "expense_count": len(group.expenses),
        "total_minor": group.total_minor,
        "total_display": format_amount(group.total_minor, exp),
        "average_expense_minor": (
            group.total_minor // len(group.expenses) if group.expenses else 0
        ),
        "largest_expense_minor": max((e.amount for e in group.expenses), default=0),
        "tense_count": len(creditors),
        "owe_count": len(debtors),
        "settled_minor": settled_amount,
        "outstanding_minor": sum(v for _, v in creditors),
        "is_balanced": sum(net.values()) == 0,
        "is_settled": not creditors and not debtors,
        "categories": {
            k: {"minor": v, "icon": CATEGORY_ICONS.get(k, "\U0001f4cc")}
            for k, v in category_totals(group).items()
        },
    }


def audit(group: Group) -> List[Dict[str, str]]:
    """Non-fatal observations worth showing the user.

    Deliberately advisory: things that are legal but usually a mistake, such as
    an expense recorded with a single participant, or a group with costs but no
    members who paid anything.
    """
    findings: List[Dict[str, str]] = []
    exp = exponent_for(group.currency)

    for bd in breakdowns(group):
        if len(bd.shares) == 1 and len(group.members) > 1:
            only = next(iter(bd.shares))
            findings.append({
                "level": "info",
                "expense": bd.expense.id,
                "message": (
                    f"'{bd.expense.description}' is charged entirely to "
                    f"{group.name_of(only)} — fine if that is intended, otherwise "
                    "add the other participants."
                ),
            })
        share_values = list(bd.shares.values())
        if share_values and max(share_values) > bd.total:
            findings.append({
                "level": "warning",
                "expense": bd.expense.id,
                "message": f"'{bd.expense.description}' has a share larger than the expense total.",
            })

    if group.expenses and not any(e.amount > 0 for e in group.expenses):
        findings.append({
            "level": "warning",
            "expense": "",
            "message": "Every expense is zero or negative — nothing to settle.",
        })

    tiny = [e.id for e in group.expenses if 0 < e.amount < 10 ** exp]
    if tiny:
        findings.append({
            "level": "info",
            "expense": tiny[0],
            "message": (
                f"{len(tiny)} expense(s) are under one whole {group.currency} unit "
                f"({format_amount(10 ** exp, exp)}) — check for a decimal-point slip."
            ),
        })

    unrecorded = [
        m.id for m in group.members
        if any(m.id in bd.shares for bd in breakdowns(group))
        and paid_totals(group).get(m.id, 0) == 0
    ]
    if unrecorded and len(unrecorded) == len(group.members):
        findings.append({
            "level": "warning",
            "expense": "",
            "message": "No member has paid anything yet.",
        })

    return findings


__all__ = [
    "Share",
    "ExpenseBreakdown",
    "breakdowns",
    "paid_totals",
    "owed_totals",
    "settled_totals",
    "balances",
    "creditors_and_debtors",
    "category_totals",
    "top_expenses",
    "member_spend_matrix",
    "health",
    "audit",
]
