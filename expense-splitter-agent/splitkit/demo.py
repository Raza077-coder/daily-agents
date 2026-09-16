"""A seeded example group, used by ``splitkit cli demo``, the tests, and the web demo.

One deliberate trap lives in here: the first expense is 100.00 split three ways
and the second is a sub-unit amount, both of which leave a remaining minor unit
that has to go *somewhere*. If any part of the engine used floats instead of
exact integer allocation the ledger would fail to net to zero, so this dataset
is the fastest way to prove the money handling is sound.

Amounts are authored in **USD cents** and then rescaled to the requested
currency's exponent, because a fixed literal like ``"0.07"`` is not
representable in a currency with no minor unit (JPY, KRW) and would crash the
demo. Expenses that scale down to zero in such a currency are dropped rather
than rounded up — the dataset stays honest instead of inventing money.
"""

from __future__ import annotations

from typing import List

from .models import Group
from .money import exponent_for
from . import storage as _storage

#: Authored in USD cents (1 USD = 100). Every amount here is exact.
_USD_CENTS: int = 100


def _scale(usd_cents: int, exponent: int) -> int:
    """Convert USD cents to the target currency's minor units, truncating.

    Truncation (not rounding) is deliberate: this is a demo fixture, and
    inventing a minor unit to make a number look rounder is exactly the habit
    the rest of SplitKit refuses to have.
    """
    return (usd_cents * (10 ** exponent)) // _USD_CENTS


def build_demo_group(currency: str = "USD") -> Group:
    """Build the canonical SplitKit demo: a three-person weekend trip."""
    exp = exponent_for(currency)
    amt = lambda cents: _scale(cents, exp)  # noqa: E731

    group = _storage.new_group(
        "Goa Weekend",
        ["Ali", "Sara", "Bilal"],
        currency=currency,
        group_id="goa-weekend",
    )
    ids = group.member_ids  # ['ali', 'sara', 'bilal']
    ali, sara, bilal = ids

    def add(desc, cents, payer, split, eid, date, category) -> bool:
        """Add an expense, skipping it if it vanishes at this currency scale."""
        value = amt(cents)
        if value <= 0:
            return False
        if split.get("mode") == "exact":
            exact = {k: amt(v) for k, v in split["amounts"].items()}
            if sum(exact.values()) != value:
                return False  # rescaling would break the exact-sum contract
            split = {**split, "amounts": exact}
        group.add_expense(desc, value, payer, split, expense_id=eid, date=date, category=category)
        return True

    add("Beach hut (2 nights)", 18000, ali, {"mode": "equal"},
        "e1", "2026-09-11", "lodging")
    # 100.00 across three people cannot divide evenly: the leftover unit has to
    # be allocated deterministically, which is exactly what this expense proves.
    add("Group dinner", 10000, sara, {"mode": "equal"},
        "e2", "2026-09-11", "food")
    # A sub-unit amount: only meaningful where the currency has minor units.
    add("Scooter fuel", 7, bilal, {"mode": "equal"},
        "e3", "2026-09-12", "transport")
    # Uneven split by shares: Ali had a double portion.
    add("Seafood platter", 9240, ali,
        {"mode": "shares", "shares": {ali: 2, sara: 1, bilal: 1}},
        "e4", "2026-09-12", "food")
    # Exact amounts that must sum to the total.
    add("Airport cab", 2400, bilal,
        {"mode": "exact", "amounts": {ali: 1200, sara: 600, bilal: 600}},
        "e5", "2026-09-12", "transport")
    # A receipt with tax and tip spread proportionally over each person's items.
    # Tax and tip are scaled with the same helper so the total still reconciles.
    items = [
        {"label": "Snacks", "amount": amt(1400), "participants": [ali, sara]},
        {"label": "Water + juice", "amount": amt(1830), "participants": [ali, sara, bilal]},
        {"label": "Sunscreen", "amount": amt(1200), "participants": [bilal]},
    ]
    tax, tip = amt(600), amt(800)
    if all(i["amount"] > 0 for i in items) and tax + tip > 0:
        group.add_expense(
            "Groceries", sum(i["amount"] for i in items) + tax + tip, sara,
            {"mode": "itemized", "items": items, "tax": tax, "tip": tip},
            expense_id="e6", date="2026-09-13", category="groceries",
        )
    # An even split with a signed adjustment that nets to zero.
    if amt(900) > 0:
        group.add_expense(
            "Late checkout fee", amt(4500), ali,
            {"mode": "adjustment", "adjustments": {ali: amt(900), sara: -amt(900)}},
            expense_id="e7", date="2026-09-13", category="lodging",
        )
    return group


def demo_matrix(currency: str = "USD") -> List[dict]:
    """A compact fixture list for parity checks between Python and JS."""
    from .engine import SplitKit

    kit = SplitKit(group=build_demo_group(currency))
    return [
        {
            "id": row["id"],
            "paid": row["paid_minor"],
            "share": row["share_minor"],
            "net": row["balance_minor"],
        }
        for row in kit.balance_table()
    ]


__all__ = ["build_demo_group", "demo_matrix"]
