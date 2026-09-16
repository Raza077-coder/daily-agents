"""Exact monetary arithmetic for SplitKit.

Every amount inside SplitKit is an ``int`` count of *minor units* — cents,
paise, fils, yen. Floating point never touches a balance, because ``0.10`` has
no exact binary representation: summing a dozen shared costs in floats drifts
by fractions of a cent until the ledger refuses to close at zero.

The one place a fraction is unavoidable is *splitting* an amount that does not
divide evenly — 100.00 across 3 people is 33.333... each. That is handled by
:func:`allocate`, a largest-remainder allocator that is **exact by
construction**: the parts always sum to the total, and the leftover minor
units are handed out deterministically rather than silently dropped.
"""

from __future__ import annotations

from fractions import Fraction
from typing import Iterable, Sequence

from .errors import MoneyError

#: Minor-unit exponent per ISO-4217 code. Most currencies have 2 decimals;
#: a few (JPY, KRW, VND, CLP, ISK) have none; the Gulf trio has three.
CURRENCY_EXPONENTS: dict[str, int] = {
    # 2 decimals
    "USD": 2, "EUR": 2, "GBP": 2, "PKR": 2, "INR": 2, "AED": 2, "SAR": 2,
    "CAD": 2, "AUD": 2, "CHF": 2, "SGD": 2, "MYR": 2, "BDT": 2, "TRY": 2,
    "ZAR": 2, "BRL": 2, "MXN": 2, "PHP": 2, "THB": 2, "IDR": 2, "NGN": 2,
    "EGP": 2, "QAR": 2, "CNY": 2, "HKD": 2, "NZD": 2, "SEK": 2, "NOK": 2,
    "DKK": 2, "PLN": 2, "CZK": 2, "HUF": 2, "RUB": 2, "UAH": 2, "LKR": 2,
    "NPR": 2, "KES": 2, "GHS": 2, "MAD": 2, "DZD": 2, "VND": 0, "ILS": 2,
    # 3 decimals
    "KWD": 3, "BHD": 3, "OMR": 3, "JOD": 3, "TND": 3, "IQD": 3, "LYD": 3,
    # 0 decimals
    "JPY": 0, "KRW": 0, "CLP": 0, "ISK": 0, "PYG": 0, "XOF": 0, "XAF": 0,
    "VUV": 0, "RWF": 0, "UGX": 0, "DJF": 0, "GNF": 0, "KMF": 0, "MGA": 0,
}

DEFAULT_EXPONENT = 2

#: Display symbols. Only used for pretty-printing; never for parsing logic
#: that depends on the symbol being unique (it is not — $ is many currencies).
SYMBOLS: dict[str, str] = {
    "USD": "$", "EUR": "\u20ac", "GBP": "\u00a3", "JPY": "\u00a5",
    "CNY": "\u00a5", "INR": "\u20b9", "PKR": "\u20a8", "AED": "\u062f.\u0625",
    "SAR": "\ufdfc", "KWD": "\u062f.\u0643", "BHD": ".\u062f.\u0628",
    "OMR": "\u0631.\u0639.", "JOD": "\u062f.\u0627", "QAR": "\u0631.\u0642",
    "BDT": "\u09f3", "TRY": "\u20ba", "ZAR": "R", "NGN": "\u20a6",
    "PHP": "\u20b1", "THB": "\u0e3f", "VND": "\u20ab", "KRW": "\u20a9",
    "CHF": "Fr", "CAD": "C$", "AUD": "A$", "NZD": "NZ$", "SGD": "S$",
    "HKD": "HK$", "BRL": "R$", "MXN": "MX$", "IDR": "Rp", "MYR": "RM",
    "ILS": "\u20aa", "PLN": "z\u0142", "CZK": "K\u010d", "SEK": "kr",
    "NOK": "kr", "DKK": "kr", "HUF": "Ft", "RUB": "\u20bd",
    "UAH": "\u20b4", "LKR": "Rs", "NPR": "Rs", "KES": "KSh", "GHS": "GH\u20b5",
}


def exponent_for(code: str) -> int:
    """Return the minor-unit exponent for an ISO-4217 code.

    Unknown codes fall back to :data:`DEFAULT_EXPONENT` (2) rather than
    raising, so a group in an unusual currency still works — the exponent is
    only ever a display/parse detail, never a correctness one.
    """
    if not isinstance(code, str):
        raise MoneyError(f"currency code must be a string, got {type(code).__name__}")
    return CURRENCY_EXPONENTS.get(code.strip().upper(), DEFAULT_EXPONENT)


def is_known_currency(code: str) -> bool:
    return isinstance(code, str) and code.strip().upper() in CURRENCY_EXPONENTS


def parse_amount(text: object, exponent: int = DEFAULT_EXPONENT) -> int:
    """Parse a human amount into integer minor units.

    Accepts ``"12.50"``, ``"$12.50"``, ``"1,234.56 USD"``, ``"12"``,
    ``"(12.50)"`` (accounting negative) and a bare ``int`` (already minor
    units). **Floats are rejected on purpose**: ``0.1 + 0.2 != 0.3`` in binary
    floating point, so accepting a float here would import the exact bug this
    module exists to prevent.

    Raises :class:`MoneyError` with a specific message rather than returning a
    silent zero — a typo'd amount is worse than a crash.
    """
    if exponent < 0 or exponent > 6:
        raise MoneyError(f"unreasonable currency exponent {exponent}")

    if isinstance(text, bool):
        raise MoneyError("refusing a boolean as an amount")
    if isinstance(text, int):
        return text
    if isinstance(text, float):
        raise MoneyError(
            f"refusing the float {text!r} as an amount — floats cannot represent "
            "most decimal amounts exactly; pass a string like \"12.50\" instead"
        )
    if text is None:
        raise MoneyError("amount is missing")
    if not isinstance(text, str):
        raise MoneyError(f"amount must be a string or int, got {type(text).__name__}")

    s = text.strip()
    if not s:
        raise MoneyError("amount is empty")

    negative = False
    if s.startswith("(") and s.endswith(")"):
        negative = True
        s = s[1:-1].strip()

    # Strip decoration we understand: currency codes (any case), known symbols,
    # underscores and internal spaces. Anything else is left in place so the
    # strict regex below rejects it instead of us guessing.
    for sym in sorted(set(SYMBOLS.values()), key=len, reverse=True):
        if sym:
            s = s.replace(sym, "")
    for code in sorted(CURRENCY_EXPONENTS, key=len, reverse=True):
        s = s.replace(code, "").replace(code.lower(), "").replace(code.title(), "")
    s = s.replace("_", "").replace(" ", "").replace("\u00a0", "")
    s = s.replace("'", "")

    if not s:
        raise MoneyError(f"amount {text!r} contains no digits")

    sign_char = ""
    if s[0] in "+-":
        sign_char, s = s[0], s[1:]
    if not s:
        raise MoneyError(f"amount {text!r} has a sign but no digits")

    body = s.replace(",", "")
    if body.count(".") > 1:
        raise MoneyError(f"amount {text!r} has more than one decimal point")

    if "." in body:
        int_part, frac_part = body.split(".", 1)
    else:
        int_part, frac_part = body, ""

    if int_part == "" and frac_part == "":
        raise MoneyError(f"amount {text!r} contains no digits")
    if int_part and not int_part.isdigit():
        raise MoneyError(f"amount {text!r} has a non-numeric integer part {int_part!r}")
    if frac_part and not frac_part.isdigit():
        raise MoneyError(f"amount {text!r} has a non-numeric decimal part {frac_part!r}")

    if len(frac_part) > exponent:
        extra = frac_part[exponent:]
        if extra.strip("0"):
            unit = "minor unit" if exponent == 1 else "minor units"
            raise MoneyError(
                f"amount {text!r} has more decimal places than the currency allows "
                f"({exponent} {unit}) — round it before recording, SplitKit will not "
                "silently drop money"
            )
        frac_part = frac_part[:exponent]

    frac_part = frac_part.ljust(exponent, "0")[:exponent]
    int_part = int_part or "0"

    value = int(int_part) * (10 ** exponent) + (int(frac_part) if exponent else 0)
    if sign_char == "-" or negative:
        value = -value
    return value


def format_amount(
    minor: int,
    exponent: int = DEFAULT_EXPONENT,
    *,
    symbol: str | None = None,
    code: str | None = None,
    grouping: bool = False,
    plus: bool = False,
) -> str:
    """Render integer minor units as a decimal string.

    ``plus=True`` prefixes a ``+`` on positive values, which is how the
    balance report makes a credit instantly readable.
    """
    if not isinstance(minor, int) or isinstance(minor, bool):
        raise MoneyError(f"format_amount needs an int of minor units, got {type(minor).__name__}")

    negative = minor < 0
    digits = str(abs(minor))

    if exponent == 0:
        whole, frac = digits, ""
    else:
        whole, frac = divmod(abs(minor), 10 ** exponent)
        whole, frac = str(whole), f"{frac:0{exponent}d}"

    if grouping:
        whole = f"{int(whole):,}"

    body = f"{whole}.{frac}" if exponent else whole
    # Zero is not positive, so a "+" on an empty balance would read as though
    # someone is owed something. Only sign genuinely positive amounts.
    sign = "-" if negative else ("+" if (plus and minor > 0) else "")
    prefix = ""
    suffix = ""
    if symbol:
        prefix = symbol
    elif code:
        suffix = f" {code}"
    return f"{sign}{prefix}{body}{suffix}"


def format_for(
    code: str, minor: int, *, symbol: bool = True, grouping: bool = True, plus: bool = False
) -> str:
    """Format minor units using a currency code's exponent and symbol."""
    exp = exponent_for(code)
    return format_amount(
        minor,
        exp,
        symbol=SYMBOLS.get(code.strip().upper()) if symbol else None,
        code=None if symbol else code,
        grouping=grouping,
        plus=plus,
    )


def allocate(total: int, weights: Sequence[Fraction | int | float | str]) -> list[int]:
    """Split ``total`` into parts proportional to ``weights``, exactly.

    Uses the largest-remainder (Hamilton) method: compute each exact fractional
    share, floor it, then hand the remaining minor units to the largest
    fractional remainders — ties broken by lowest index so the result is fully
    deterministic. ``sum(result) == total`` always holds, including for negative
    totals (refunds) and for weights that are themselves fractional.

    Zero-weight entries receive zero, which is what you want: a person who was
    not part of an item should never absorb a rounding cent from it.
    """
    if not isinstance(total, int) or isinstance(total, bool):
        raise MoneyError(f"allocate needs an int total, got {type(total).__name__}")
    if not weights:
        raise MoneyError("allocate() needs at least one weight")

    fractions: list[Fraction] = []
    for i, raw in enumerate(weights):
        if isinstance(raw, bool):
            raise MoneyError(f"weight {i} is a boolean")
        if isinstance(raw, float):
            # Fraction(float) is exact-to-the-binary-value, which keeps the sum
            # invariant intact; callers wanting decimal exactness pass strings.
            frac = Fraction(raw)
        elif isinstance(raw, Fraction):
            frac = raw
        else:
            try:
                frac = Fraction(raw)
            except (TypeError, ZeroDivisionError, ValueError) as exc:
                raise MoneyError(f"weight {i} ({raw!r}) is not a number: {exc}") from exc
        if frac < 0:
            raise MoneyError(f"weight {i} is negative ({raw!r}); weights must be >= 0")
        fractions.append(frac)

    weight_sum = sum(fractions)
    if weight_sum <= 0:
        raise MoneyError("weights sum to zero — nothing to allocate by")

    if total == 0:
        return [0] * len(fractions)

    sign = -1 if total < 0 else 1
    magnitude = abs(total)

    shares = [Fraction(magnitude) * w / weight_sum for w in fractions]
    parts = [s.numerator // s.denominator for s in shares]
    remainders = [s - p for s, p in zip(shares, parts)]

    shortfall = magnitude - sum(parts)
    if shortfall:
        order = sorted(range(len(fractions)), key=lambda i: (-remainders[i], i))
        for i in order[:shortfall]:
            parts[i] += 1

    if sum(parts) != magnitude:  # pragma: no cover - defensive
        raise MoneyError(
            f"allocation failed to conserve money: {sum(parts)} != {magnitude}"
        )
    return [sign * p for p in parts]


def split_evenly(total: int, count: int) -> list[int]:
    """Split ``total`` into ``count`` equal parts, distributing the residue."""
    if count < 1:
        raise MoneyError(f"cannot split {total} across {count} people")
    return allocate(total, [1] * count)


def allocate_by_ratio(total: int, ratios: Sequence[Fraction | int | float | str]) -> list[int]:
    """Alias for :func:`allocate` with a name that reads better at call sites."""
    return allocate(total, ratios)


def sum_minor(values: Iterable[int]) -> int:
    """Sum minor-unit values, rejecting anything that is not an ``int``."""
    total = 0
    for v in values:
        if not isinstance(v, int) or isinstance(v, bool):
            raise MoneyError(f"expected int minor units, got {type(v).__name__}")
        total += v
    return total


def parse_percent(text: object) -> Fraction:
    """Parse ``"33.33"`` / ``"33.33%"`` into a :class:`Fraction` of percent.

    Percentages are compared as exact fractions, so three entries of
    ``"33.33"`` plus ``"0.01"`` reads as exactly 100 and does not fall foul of
    float comparison.
    """
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        raise MoneyError(
            f"refusing the numeric percent {text!r} — pass a string like \"33.33\" "
            "so the value is exact"
        )
    if not isinstance(text, str):
        raise MoneyError("percent must be a string")
    s = text.strip().rstrip("%").strip()
    if not s:
        raise MoneyError("percent is empty")
    try:
        value = Fraction(s)
    except (ValueError, ZeroDivisionError) as exc:
        raise MoneyError(f"cannot read {text!r} as a percent: {exc}") from exc
    if value < 0:
        raise MoneyError(f"percent {text!r} is negative")
    return value


__all__ = [
    "CURRENCY_EXPONENTS",
    "DEFAULT_EXPONENT",
    "SYMBOLS",
    "exponent_for",
    "is_known_currency",
    "parse_amount",
    "parse_percent",
    "format_amount",
    "format_for",
    "allocate",
    "allocate_by_ratio",
    "split_evenly",
    "sum_minor",
]
