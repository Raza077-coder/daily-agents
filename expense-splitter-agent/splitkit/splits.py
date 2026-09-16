"""The six split algorithms.

Each mode answers the same question — *how much of this expense belongs to
each person?* — and every one of them returns integer minor units that sum
**exactly** to the expense total. The modes differ only in how you describe
the shares:

===============  ============================================================
``equal``        Everyone in ``participants`` pays the same amount.
``exact``        You name each person's amount; it must sum to the total.
``shares``       Weighted parts ("A had 2 shares, B had 1").
``percent``      Percentages that must add to exactly 100.
``itemized``     Per-item assignment, then tax/tip spread over the items.
``adjustment``   Equal base, then signed tweaks that must net to zero.
===============  ============================================================

The reason each mode has a strict validator rather than a "best effort" path:
a split that silently absorbs a discrepancy produces a ledger that looks fine
and is wrong. :func:`resolve` therefore refuses ambiguity and says exactly
what is off — ``exact amounts sum to 45.00, expense is 45.50``.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Iterable, List, Mapping, Sequence

from .errors import MoneyError, SplitError, UnknownMemberError, ValidationError
from .money import (
    allocate,
    exponent_for,
    format_amount,
    parse_amount,
    parse_percent,
)

#: Every mode this module understands.
MODES = ("equal", "exact", "shares", "percent", "itemized", "adjustment")

#: One-line explanation of each mode, used by ``--help`` and the docs endpoint.
MODE_DESCRIPTIONS: Dict[str, str] = {
    "equal": "Split evenly across the listed participants (default: everyone in the group).",
    "exact": "Give each person an exact amount; the amounts must sum to the expense total.",
    "shares": "Split by weights — 2 shares and 1 share is a two-thirds / one-third split.",
    "percent": "Split by percentage; the percentages must add up to exactly 100.",
    "itemized": "Assign each line item to the people who consumed it, then spread tax and tip.",
    "adjustment": "Start from an equal split, then apply signed per-person adjustments that net to zero.",
}


def _norm_members(seq: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for raw in seq:
        ident = str(raw).strip().lower()
        if not ident:
            raise SplitError("participant ids must be non-empty")
        if ident in out:
            raise SplitError(f"duplicate participant {ident!r}")
        out.append(ident)
    return out


def _check_known(ids: Sequence[str], known: Sequence[str], what: str) -> None:
    known_set = set(known)
    unknown = [i for i in ids if i not in known_set]
    if unknown:
        raise UnknownMemberError(
            f"{what} references {', '.join(repr(u) for u in unknown)}, "
            f"who {'is' if len(unknown) == 1 else 'are'} not in the group "
            f"(members: {', '.join(known)})"
        )


def _mapping(spec: Mapping[str, Any], key: str, mode: str) -> Dict[str, Any]:
    raw = spec.get(key)
    if raw is None:
        raise SplitError(f"a {mode!r} split needs a {key!r} object")
    if not isinstance(raw, dict):
        raise SplitError(f"{key!r} must be an object of member -> value, got {type(raw).__name__}")
    if not raw:
        raise SplitError(f"{key!r} is empty — a split needs at least one entry")
    return {str(k).strip().lower(): v for k, v in raw.items()}


def participants_of(spec: Mapping[str, Any], all_members: Sequence[str]) -> List[str]:
    """Resolve the participant list, defaulting to the whole group."""
    raw = spec.get("participants")
    if raw is None:
        return list(all_members)
    if isinstance(raw, str):
        raise SplitError("'participants' must be a list of member ids, not a single string")
    ids = _norm_members(raw)
    if not ids:
        raise SplitError("'participants' is empty — a split needs at least one participant")
    _check_known(ids, all_members, "the split")
    return ids


def resolve(
    spec: Mapping[str, Any],
    amount: int,
    all_members: Sequence[str],
    *,
    currency: str = "USD",
) -> Dict[str, int]:
    """Resolve a split spec into ``{member_id: minor_units}``.

    The returned map omits members who owe exactly zero, and its values always
    sum to ``amount``. Raises :class:`SplitError` / :class:`UnknownMemberError`
    on any inconsistency — never returns a best-guess split.

    Amount and percentage parsing failures are re-raised as
    :class:`SplitError` so a caller catching that one type sees every way a
    split can be rejected, instead of having to also know about
    :class:`MoneyError`.
    """
    try:
        return _resolve_impl(spec, amount, all_members, currency=currency)
    except MoneyError as exc:
        raise SplitError(str(exc)) from exc


def _resolve_impl(
    spec: Mapping[str, Any],
    amount: int,
    all_members: Sequence[str],
    *,
    currency: str = "USD",
) -> Dict[str, int]:
    """The actual resolver behind :func:`resolve`."""
    if not isinstance(spec, Mapping):
        raise SplitError(f"a split must be an object, got {type(spec).__name__}")
    if not isinstance(amount, int) or isinstance(amount, bool):
        raise SplitError("split amount must be integer minor units")

    mode = str(spec.get("mode") or "equal").strip().lower()
    if mode not in MODES:
        raise SplitError(f"unknown split mode {mode!r} — expected one of {', '.join(MODES)}")

    exponent = exponent_for(currency)
    if amount == 0:
        return {}

    if mode == "equal":
        people = participants_of(spec, all_members)
        parts = allocate(amount, [1] * len(people))
        return _compact(dict(zip(people, parts)))

    if mode == "exact":
        exp = exponent_for(currency)
        mapping = _mapping(spec, "amounts", mode)
        _check_known(list(mapping), all_members, "the split")
        values: Dict[str, int] = {}
        for who, raw in mapping.items():
            if isinstance(raw, float):
                raise SplitError(
                    f"exact amount for {who!r} is a float — pass a string like \"12.50\""
                )
            values[who] = raw if isinstance(raw, int) else parse_amount(raw, exp)
        total = sum(values.values())
        if total != amount:
            diff = amount - total
            raise SplitError(
                f"exact amounts sum to {format_amount(total, exp)} but the expense is "
                f"{format_amount(amount, exp)} — off by {format_amount(diff, exp, plus=True)}. "
                "Adjust the amounts (or use 'adjustment' mode) so they match exactly."
            )
        return _compact(values)

    if mode == "shares":
        mapping = _mapping(spec, "shares", mode)
        _check_known(list(mapping), all_members, "the split")
        weights: List[Fraction] = []
        people: List[str] = []
        for who, raw in mapping.items():
            if isinstance(raw, bool):
                raise SplitError(f"share for {who!r} is a boolean")
            try:
                frac = Fraction(raw) if not isinstance(raw, float) else Fraction(str(raw))
            except (TypeError, ValueError, ZeroDivisionError) as exc:
                raise SplitError(f"share for {who!r} ({raw!r}) is not a number: {exc}") from exc
            if frac < 0:
                raise SplitError(f"share for {who!r} is negative ({raw!r})")
            weights.append(frac)
            people.append(who)
        if sum(weights) <= 0:
            raise SplitError("shares sum to zero — at least one share must be positive")
        parts = allocate(amount, weights)
        return _compact(dict(zip(people, parts)))

    if mode == "percent":
        mapping = _mapping(spec, "percents", mode)
        _check_known(list(mapping), all_members, "the split")
        percents: Dict[str, Fraction] = {}
        for who, raw in mapping.items():
            percents[who] = parse_percent(raw)
        total = sum(percents.values())
        if total != 100:
            raise SplitError(
                f"percents sum to {_frac_str(total)}% but must be exactly 100% "
                f"({format_amount(amount, exponent)} to hand out); "
                f"adjust by {_frac_str(100 - total)}%"
            )
        people = list(percents)
        parts = allocate(amount, [percents[p] for p in people])
        return _compact(dict(zip(people, parts)))

    if mode == "itemized":
        return _resolve_itemized(spec, amount, all_members, currency)

    # adjustment
    return _resolve_adjustment(spec, amount, all_members, currency)


def _frac_str(value: Fraction) -> str:
    """Render a Fraction as a short decimal string for error messages."""
    if value.denominator == 1:
        return str(value.numerator)
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _compact(values: Mapping[str, int]) -> Dict[str, int]:
    """Drop zero-share entries while preserving insertion order."""
    return {k: v for k, v in values.items() if v != 0}


def _resolve_itemized(
    spec: Mapping[str, Any],
    amount: int,
    all_members: Sequence[str],
    currency: str,
) -> Dict[str, int]:
    """Per-item assignment with tax and tip spread across the item subtotals.

    The model that matches how a real receipt works: tax and tip are
    proportional to what each person ordered. ``tax_mode`` / ``tip_mode`` may
    be set to ``"equal"`` to spread them evenly instead — the useful choice
    when the table shared a flat service charge.
    """
    exp = exponent_for(currency)
    raw_items = spec.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise SplitError("an 'itemized' split needs a non-empty 'items' list")

    totals: Dict[str, int] = {}
    item_subtotal = 0
    for idx, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            raise SplitError(f"item {idx} must be an object with label/amount/participants")
        label = str(raw.get("label") or raw.get("description") or f"item {idx + 1}").strip()
        raw_amount = raw.get("amount")
        if raw_amount is None:
            raise SplitError(f"item {idx} ({label!r}) is missing an amount")
        if isinstance(raw_amount, float):
            raise SplitError(f"item {idx} ({label!r}) amount is a float — pass a string")
        amount_i = (
            raw_amount if isinstance(raw_amount, int) else parse_amount(raw_amount, exp)
        )
        if amount_i < 0:
            raise SplitError(f"item {idx} ({label!r}) has a negative amount")
        raw_parts = raw.get("participants")
        if raw_parts is None:
            people = list(all_members)
        else:
            if isinstance(raw_parts, str):
                raise SplitError(f"item {idx} ({label!r}) participants must be a list")
            people = _norm_members(raw_parts)
            if not people:
                raise SplitError(f"item {idx} ({label!r}) has no participants")
            _check_known(people, all_members, f"item {idx} ({label!r})")
        shares = allocate(amount_i, [1] * len(people))
        for who, part in zip(people, shares):
            totals[who] = totals.get(who, 0) + part
        item_subtotal += amount_i

    extras: Dict[str, int] = {}
    for key, mode_key in (("tax", "tax_mode"), ("tip", "tip_mode")):
        raw = spec.get(key)
        if raw is None or raw == 0 or raw == "0":
            extras[key] = 0
            continue
        if isinstance(raw, float):
            raise SplitError(f"{key} is a float — pass a string amount like \"4.80\"")
        val = raw if isinstance(raw, int) else parse_amount(raw, exp)
        if val < 0:
            raise SplitError(f"{key} cannot be negative ({format_amount(val, exp)})")
        extras[key] = val

    extras_total = extras["tax"] + extras["tip"]
    if item_subtotal + extras_total != amount:
        raise SplitError(
            f"items ({format_amount(item_subtotal, exp)}) + tax "
            f"({format_amount(extras['tax'], exp)}) + tip "
            f"({format_amount(extras['tip'], exp)}) = "
            f"{format_amount(item_subtotal + extras_total, exp)}, but the expense is "
            f"{format_amount(amount, exp)}. Set the expense amount to the receipt total."
        )

    if extras_total:
        # Proportional to what each person ordered; zero-subtotal people are
        # excluded so they never absorb a rounding unit from someone else.
        for key, mode_key in (("tax", "tax_mode"), ("tip", "tip_mode")):
            pot = extras[key]
            if not pot:
                continue
            mode = str(spec.get(mode_key) or "proportional").strip().lower()
            if mode not in ("proportional", "equal"):
                raise SplitError(
                    f"{mode_key} must be 'proportional' or 'equal', got {mode!r}"
                )
            if mode == "equal":
                people = [p for p in totals if totals[p] > 0] or list(all_members)
                for who, part in zip(people, allocate(pot, [1] * len(people))):
                    totals[who] = totals.get(who, 0) + part
            else:
                people = [p for p in totals if totals[p] > 0]
                if not people:
                    people = list(all_members)
                    weights = [1] * len(people)
                else:
                    weights = [totals[p] for p in people]
                for who, part in zip(people, allocate(pot, weights)):
                    totals[who] = totals.get(who, 0) + part

    return _compact(totals)


def _resolve_adjustment(
    spec: Mapping[str, Any],
    amount: int,
    all_members: Sequence[str],
    currency: str,
) -> Dict[str, int]:
    """Equal base split, then signed per-person tweaks that must cancel out.

    Handy for "we split the villa evenly, but Sam skipped two nights so he
    takes 30 off" — the adjustments net to zero, so the expense still balances
    without anyone hand-editing the other shares.
    """
    exp = exponent_for(currency)
    people = participants_of(spec, all_members)
    base = dict(zip(people, allocate(amount, [1] * len(people))))

    raw_adj = spec.get("adjustments") or {}
    if not isinstance(raw_adj, dict):
        raise SplitError("'adjustments' must be an object of member -> signed amount")
    adj: Dict[str, int] = {}
    for who, raw in raw_adj.items():
        key = str(who).strip().lower()
        if isinstance(raw, float):
            raise SplitError(f"adjustment for {key!r} is a float — pass a string like \"-30.00\"")
        adj[key] = raw if isinstance(raw, int) else parse_amount(raw, exp)

    _check_known(list(adj), all_members, "the adjustments")
    stray = [k for k in adj if k not in base]
    if stray:
        raise SplitError(
            f"adjustment for {', '.join(repr(s) for s in stray)} — but "
            f"{'they are' if len(stray) > 1 else 'that person is'} not in 'participants' "
            f"({', '.join(people)}); add them to participants or drop the adjustment"
        )

    total_adj = sum(adj.values())
    if total_adj != 0:
        raise SplitError(
            f"adjustments sum to {format_amount(total_adj, exp, plus=True)} — they must "
            "net to zero so the expense still balances (what one person is credited, "
            "another must be charged)"
        )

    for who, delta in adj.items():
        base[who] = base.get(who, 0) + delta

    negatives = {k: v for k, v in base.items() if v < 0}
    if negatives:
        detail = ", ".join(f"{k} {format_amount(v, exp)}" for k, v in negatives.items())
        raise SplitError(
            f"adjustments push {detail} below zero — the largest single adjustment "
            "cannot exceed that person's equal share"
        )
    return _compact(base)


def describe(spec: Mapping[str, Any], name_of: Any, currency: str = "USD") -> str:
    """Human sentence explaining a split, for reports and the browser HUD."""
    if not isinstance(spec, Mapping):
        return "shared (unknown rule)"
    exp = exponent_for(currency)
    mode = str(spec.get("mode") or "equal").strip().lower()
    fmt = lambda v: format_amount(v if isinstance(v, int) else parse_amount(v, exp), exp)  # noqa: E731

    if mode == "equal":
        parts = spec.get("participants")
        if not parts:
            return "split evenly between everyone"
        names = ", ".join(name_of(str(p)) for p in parts)
        return f"split evenly between {names}"
    if mode == "exact":
        bits = ", ".join(
            f"{name_of(str(k))} {fmt(v)}" for k, v in (spec.get("amounts") or {}).items()
        )
        return f"exact amounts: {bits}"
    if mode == "shares":
        bits = ", ".join(
            f"{name_of(str(k))} {v} share{'s' if str(v) != '1' else ''}"
            for k, v in (spec.get("shares") or {}).items()
        )
        return f"by shares: {bits}"
    if mode == "percent":
        bits = ", ".join(
            f"{name_of(str(k))} {parse_percent(v)}%" for k, v in (spec.get("percents") or {}).items()
        )
        return f"by percentage: {bits}"
    if mode == "itemized":
        items = spec.get("items") or []
        bits = []
        for it in items:
            if not isinstance(it, dict):
                continue
            parts = it.get("participants")
            who = ", ".join(name_of(str(p)) for p in parts) if parts else "everyone"
            bits.append(f"{it.get('label')} ({fmt(it.get('amount'))}) to {who}")
        extra = []
        if spec.get("tax"):
            extra.append(f"tax {fmt(spec['tax'])}")
        if spec.get("tip"):
            extra.append(f"tip {fmt(spec['tip'])}")
        tail = f" + {' + '.join(extra)}" if extra else ""
        return f"itemized: {'; '.join(bits)}{tail}"
    if mode == "adjustment":
        bits = ", ".join(
            f"{name_of(str(k))} {format_amount(parse_amount(v, exp) if not isinstance(v, int) else v, exp, plus=True)}"
            for k, v in (spec.get("adjustments") or {}).items()
        )
        return f"even split, adjusted: {bits}" if bits else "even split, adjusted"
    return f"{mode} split"


__all__ = ["MODES", "MODE_DESCRIPTIONS", "resolve", "describe", "participants_of"]
