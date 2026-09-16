"""Settle-up: turn net balances into the fewest transfers that clear the book.

Two strategies, because "fewest" means different things in different rooms:

* **greedy** — repeatedly match the biggest debtor against the biggest
  creditor. Simple, obviously fair, and produces at most ``n-1`` transfers.
  This is what almost every split app does.
* **optimal** — minimum *number of transfers*. The general problem is
  NP-hard, but the standard exact approach works beautifully at group sizes: a
  zero-sum balance set can be partitioned into independent subsets, each of
  which settles internally in ``k-1`` transfers. Maximising the number of
  parts therefore minimises transfers, and the partition is found with a DP
  over bitmasks (``O(3^n)`` subgroups via ``2^n`` sums, comfortable to n=20).

:func:`settle` runs both and reports the saving, so the plan you show someone
can say "3 transfers instead of 5".
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .errors import SettleError, ValidationError
from .ledger import balances
from .models import Group
from .money import exponent_for, format_amount

#: Above this many members the bitmask DP is skipped (2^20 subsets is already
#: 1M; beyond that the greedy plan is returned alone with a note).
OPTIMAL_MEMBER_LIMIT = 20


@dataclass
class Transfer:
    """A single payment: ``from_member`` sends ``amount`` to ``to_member``."""

    from_member: str
    to_member: str
    amount: int

    def to_dict(self) -> Dict[str, Any]:
        return {"from": self.from_member, "to": self.to_member, "amount": self.amount}


@dataclass
class SettlePlan:
    """A complete settle-up plan plus a comparison against the alternative."""

    transfers: List[Transfer]
    strategy: str = "greedy"
    greedy_count: int = 0
    optimal_count: int = 0
    optimal_feasible: bool = True
    notes: List[str] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.transfers)

    @property
    def saved(self) -> int:
        return max(self.greedy_count - self.optimal_count, 0)

    def to_dict(self, currency: str = "USD") -> Dict[str, Any]:
        exp = exponent_for(currency)
        return {
            "strategy": self.strategy,
            "transfer_count": self.count,
            "greedy_count": self.greedy_count,
            "optimal_count": self.optimal_count,
            "optimal_feasible": self.optimal_feasible,
            "transfers_saved": self.saved,
            "total_moved_minor": sum(t.amount for t in self.transfers),
            "transfers": [
                {
                    **t.to_dict(),
                    "amount_display": format_amount(t.amount, exp),
                }
                for t in self.transfers
            ],
            "notes": list(self.notes),
        }


def greedy_transfers(net: Mapping[str, int]) -> List[Transfer]:
    """Match largest debtor to largest creditor until the book is clear.

    Deterministic: candidates are re-sorted after every transfer with an
    alphabetical tie-break, so two runs always produce the same plan.
    """
    creditors = sorted(((m, v) for m, v in net.items() if v > 0), key=lambda kv: (-kv[1], kv[0]))
    debtors = sorted(((m, v) for m, v in net.items() if v < 0), key=lambda kv: (kv[1], kv[0]))
    creditors = [list(x) for x in creditors]
    debtors = [list(x) for x in debtors]

    transfers: List[Transfer] = []
    guard = 0
    limit = 4 * (len(creditors) + len(debtors)) + 16
    while creditors and debtors:
        guard += 1
        if guard > limit:  # pragma: no cover - defensive
            raise SettleError("settle-up failed to converge — the ledger is inconsistent")

        ci = min(range(len(creditors)), key=lambda i: (-creditors[i][1], creditors[i][0]))
        di = min(range(len(debtors)), key=lambda i: (debtors[i][1], debtors[i][0]))
        credit = creditors[ci][1]
        debt = -debtors[di][1]
        amount = min(credit, debt)
        if amount <= 0:  # pragma: no cover - defensive
            raise SettleError("settle-up produced a non-positive transfer")

        transfers.append(
            Transfer(from_member=debtors[di][0], to_member=creditors[ci][0], amount=amount)
        )
        creditors[ci][1] -= amount
        debtors[di][1] += amount
        creditors = [c for c in creditors if c[1] > 0]
        debtors = [d for d in debtors if d[1] < 0]

    return transfers


def _zero_sum_parts(values: Sequence[int], limit_subsets: int = 1 << 20) -> Optional[List[List[int]]]:
    """Partition indices into the most zero-sum groups found, or ``None``.

    Returns ``None`` when the DP would be too large, so the caller can fall back
    to greedy instead of hanging on a 25-person group.
    """
    n = len(values)
    size = 1 << n
    if size > limit_subsets:
        return None

    sums = [0] * size
    for mask in range(1, size):
        low = mask & -mask
        idx = low.bit_length() - 1
        sums[mask] = sums[mask ^ low] + values[idx]

    # best[mask] = (max parts, the submask that is the final part)
    best: List[Tuple[int, int]] = [(-1, 0)] * size
    best[0] = (0, 0)
    for mask in range(1, size):
        low = mask & -mask
        sub = mask
        best_here = (-1, 0)
        while sub:
            if sub & low and sums[sub] == 0:
                rest, _ = best[mask ^ sub]
                if rest >= 0 and rest + 1 > best_here[0]:
                    best_here = (rest + 1, sub)
            sub = (sub - 1) & mask
        best[mask] = best_here

    full = (1 << n) - 1
    if best[full][0] <= 0:
        return None

    parts: List[List[int]] = []
    mask = full
    while mask:
        _, sub = best[mask]
        if sub == 0:  # pragma: no cover - defensive
            return None
        parts.append([i for i in range(n) if sub & (1 << i)])
        mask ^= sub
    return parts


def optimal_transfers(net: Mapping[str, int]) -> Tuple[List[Transfer], bool]:
    """Fewest-possible transfers. Returns ``(transfers, was_exact)``.

    Splits the members into independent zero-sum groups and settles each group
    internally — an isolated sub-group settles in ``k-1`` transfers, so more
    groups means fewer payments overall. ``was_exact=False`` means the DP was
    skipped as too large and the result is the plain greedy plan.
    """
    members = sorted(net)
    values = [net[m] for m in members]
    if not any(values):
        return [], True

    # Members at exactly zero never need to move money; excluding them keeps
    # the bitmask small and cannot change the answer.
    active = [i for i, v in enumerate(values) if v != 0]
    if not active:
        return [], True

    sub_values = [values[i] for i in active]
    parts = _zero_sum_parts(sub_values)
    if parts is None:
        return greedy_transfers(net), False

    transfers: List[Transfer] = []
    for part in parts:
        local = {members[active[i]]: sub_values[i] for i in part}
        local_total = sum(local.values())
        if local_total != 0:  # pragma: no cover - partition is zero-sum by construction
            raise SettleError(
                f"internal error: partition group sums to {local_total}, expected 0"
            )
        transfers.extend(greedy_transfers(local))

    transfers.sort(key=lambda t: (t.from_member, t.to_member, t.amount))
    return transfers, True


def settle(
    group: Group,
    *,
    strategy: str = "optimal",
    min_transfer: int = 0,
) -> SettlePlan:
    """Produce the settle-up plan for a group.

    ``strategy`` is ``"greedy"``, ``"optimal"`` or ``"compare"`` (run both and
    return the better one, defaulting to optimal). ``min_transfer`` is a
    threshold in minor units: transfers between the same pair are merged, and
    anything still at or below the threshold is reported in ``notes`` — the
    plan stays exact rather than silently dropping a debt.
    """
    if strategy not in ("greedy", "optimal", "compare"):
        raise ValidationError(f"strategy must be greedy|optimal|compare, got {strategy!r}")

    net = balances(group)
    greedy = greedy_transfers(net)
    optimal, exact = optimal_transfers(net) if len(group.members) else ([], True)

    notes: List[str] = []
    if not exact:
        notes.append(
            f"Group has {len(group.members)} members — above {OPTIMAL_MEMBER_LIMIT} the "
            "exact minimum-transfer search is skipped and the greedy plan is used."
        )

    if strategy == "greedy":
        chosen, label = greedy, "greedy"
    elif strategy == "optimal":
        chosen, label = (optimal, "optimal") if exact else (greedy, "greedy")
    else:
        if exact and len(optimal) < len(greedy):
            chosen, label = optimal, "optimal"
        else:
            chosen, label = greedy, "greedy"

    if min_transfer > 0:
        chosen, flagged = _apply_min_transfer(net, chosen, min_transfer)
        if flagged:
            notes.append(
                f"{flagged} transfer(s) are at or below "
                f"{format_amount(min_transfer, exponent_for(group.currency))}. They are "
                "kept so the ledger stays exact — settle them in cash, or lower "
                "min_transfer to ignore this."
            )

    plan = SettlePlan(
        transfers=chosen,
        strategy=label,
        greedy_count=len(greedy),
        optimal_count=len(optimal) if exact else len(greedy),
        optimal_feasible=exact,
        notes=notes,
    )
    if len(plan.transfers) == 0:
        plan.notes.append("Everyone is square — nothing to settle.")
    return plan


def _apply_min_transfer(
    net: Mapping[str, int], transfers: List[Transfer], threshold: int
) -> Tuple[List[Transfer], int]:
    """Combine same-counterparty transfers and flag the ones still small.

    Deleting a small transfer outright would leave the ledger unbalanced, and
    re-settling the parties afterwards simply regenerates the same transfer —
    a small debt between two people genuinely has to be paid by those two
    people. So this collapses transfers that share a payer/payee pair (the only
    reduction that is exactly lossless) and counts whatever is still at or
    below the threshold so the caller can mention it rather than hide it.
    """
    merged: Dict[Tuple[str, str], int] = {}
    for t in transfers:
        key = (t.from_member, t.to_member)
        merged[key] = merged.get(key, 0) + t.amount

    out = [
        Transfer(from_member=f, to_member=to, amount=a)
        for (f, to), a in sorted(merged.items())
        if a > 0
    ]
    flagged = [t for t in out if t.amount <= threshold]
    return out, len(flagged)


def verify_plan(net: Mapping[str, int], transfers: Sequence[Transfer]) -> None:
    """Assert a plan actually clears every balance. Raises on any mismatch.

    Used by the tests and by the API before returning a plan, because a
    settle-up that looks tidy but leaves someone 0.01 out is worse than none.
    """
    after = dict(net)
    for t in transfers:
        if t.amount <= 0:
            raise SettleError(f"transfer {t.from_member}->{t.to_member} is not positive")
        if t.from_member == t.to_member:
            raise SettleError("a transfer cannot be to the same person")
        if after.get(t.from_member, 0) >= 0:
            raise SettleError(
                f"{t.from_member} is sending {t.amount} but their balance is "
                f"{after.get(t.from_member, 0)} (not a debtor)"
            )
        if after.get(t.to_member, 0) <= 0:
            raise SettleError(
                f"{t.to_member} is receiving {t.amount} but their balance is "
                f"{after.get(t.to_member, 0)} (not a creditor)"
            )
        after[t.from_member] = after.get(t.from_member, 0) + t.amount
        after[t.to_member] = after.get(t.to_member, 0) - t.amount

    leftover = {k: v for k, v in after.items() if v != 0}
    # Checked even for an empty plan: a book that is not square must never be
    # reported as settled just because there were no transfers to inspect.
    if leftover:
        raise SettleError(f"plan left unsettled balances: {leftover}")


def lower_bound(net: Mapping[str, int]) -> int:
    """A cheap lower bound on the minimum number of transfers.

    ``max(creditors, debtors)`` is a classic bound; it is used in the tests to
    prove the optimal solver never returns fewer transfers than is possible.
    """
    creditors = sum(1 for v in net.values() if v > 0)
    debtors = sum(1 for v in net.values() if v < 0)
    return max(creditors, debtors)


__all__ = [
    "Transfer",
    "SettlePlan",
    "greedy_transfers",
    "optimal_transfers",
    "settle",
    "verify_plan",
    "lower_bound",
    "OPTIMAL_MEMBER_LIMIT",
]
