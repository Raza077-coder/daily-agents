"""Tests for exact monetary arithmetic — parsing, formatting, allocation."""

from __future__ import annotations

from fractions import Fraction

import pytest

from splitkit.errors import MoneyError
from splitkit.money import (
    CURRENCY_EXPONENTS,
    allocate,
    exponent_for,
    format_amount,
    format_for,
    is_known_currency,
    parse_amount,
    parse_percent,
    split_evenly,
    sum_minor,
)


# --------------------------------------------------------------------------- #
# exponents
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "code,expected",
    [
        ("USD", 2), ("EUR", 2), ("GBP", 2), ("PKR", 2), ("INR", 2),
        ("JPY", 0), ("KRW", 0), ("VND", 0), ("ISK", 0), ("CLP", 0),
        ("KWD", 3), ("BHD", 3), ("OMR", 3), ("JOD", 3),
    ],
)
def test_exponent_for_known_currencies(code, expected):
    assert exponent_for(code) == expected


def test_exponent_is_case_and_space_insensitive():
    assert exponent_for("  usd  ") == 2
    assert exponent_for("jpy") == 0


def test_unknown_currency_falls_back_to_two_decimals():
    assert exponent_for("XYZ") == 2
    assert exponent_for("ABC") == 2


def test_exponent_rejects_non_string():
    with pytest.raises(MoneyError):
        exponent_for(123)  # type: ignore[arg-type]


def test_is_known_currency():
    assert is_known_currency("USD") is True
    assert is_known_currency("usd") is True
    assert is_known_currency("ZZZ") is False
    assert is_known_currency(None) is False  # type: ignore[arg-type]


def test_currency_table_is_non_empty_and_sane():
    assert len(CURRENCY_EXPONENTS) > 50
    for code, exp in CURRENCY_EXPONENTS.items():
        assert isinstance(code, str) and len(code) == 3 and code.isupper()
        assert exp in (0, 2, 3)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("0", 0), ("1", 100), ("12", 1200), ("12.5", 1250), ("12.50", 1250),
        ("12.05", 1205), ("0.01", 1), ("0.10", 10), ("1000.00", 100000),
        ("1,234.56", 123456), ("$12.50", 1250), ("12.50 USD", 1250),
        ("USD 12.50", 1250), ("+3.00", 300), ("-3.00", -300),
        ("(12.50)", -1250), ("(0.01)", -1), (" 7.25 ", 725),
        ("1_000.00", 100000), ("12.500", 1250), ("0.00", 0),
    ],
)
def test_parse_amount_good_values(text, expected):
    assert parse_amount(text) == expected


@pytest.mark.parametrize("value,expected", [(0, 0), (1, 1), (1250, 1250), (-99, -99)])
def test_parse_amount_accepts_ints_as_minor_units(value, expected):
    assert parse_amount(value) == expected


def test_parse_amount_rejects_float_because_floats_lose_money():
    with pytest.raises(MoneyError) as err:
        parse_amount(12.5)  # type: ignore[arg-type]
    assert "float" in str(err.value).lower()


def test_parse_amount_rejects_boolean():
    with pytest.raises(MoneyError):
        parse_amount(True)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [None, "", "   ", "abc", "12.3.4", "12,3.4.5", "-", "+", "$"])
def test_parse_amount_rejects_junk(bad):
    with pytest.raises(MoneyError):
        parse_amount(bad)  # type: ignore[arg-type]


def test_parse_amount_rejects_extra_precision_rather_than_rounding():
    with pytest.raises(MoneyError) as err:
        parse_amount("12.345")
    assert "decimal places" in str(err.value)


def test_parse_amount_allows_trailing_zeros_beyond_precision():
    assert parse_amount("12.500") == 1250


def test_parse_amount_honours_currency_exponent():
    assert parse_amount("12", exponent=0) == 12
    assert parse_amount("12.345", exponent=3) == 12345
    with pytest.raises(MoneyError):
        parse_amount("12.5", exponent=0)


def test_parse_amount_rejects_absurd_exponent():
    with pytest.raises(MoneyError):
        parse_amount("1", exponent=99)


def test_parse_amount_reports_non_numeric_parts_clearly():
    with pytest.raises(MoneyError) as err:
        parse_amount("12a.50")
    assert "12a" in str(err.value)


# --------------------------------------------------------------------------- #
# formatting
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "minor,exp,expected",
    [
        (0, 2, "0.00"), (1, 2, "0.01"), (1250, 2, "12.50"), (-1250, 2, "-12.50"),
        (100000, 2, "1000.00"), (12, 0, "12"), (12345, 3, "12.345"),
        (5, 0, "5"),
    ],
)
def test_format_amount_values(minor, exp, expected):
    assert format_amount(minor, exp) == expected


def test_format_amount_grouping_and_plus():
    assert format_amount(123456789, 2, grouping=True) == "1,234,567.89"
    assert format_amount(1250, 2, plus=True) == "+12.50"
    assert format_amount(-1250, 2, plus=True) == "-12.50"
    assert format_amount(0, 2, plus=True) == "0.00"


def test_format_amount_symbol_and_code():
    assert format_amount(1250, 2, symbol="$") == "$12.50"
    assert format_amount(1250, 2, code="USD") == "12.50 USD"
    assert format_amount(-1250, 2, symbol="$") == "-$12.50"


def test_format_amount_rejects_non_int():
    with pytest.raises(MoneyError):
        format_amount("12.50")  # type: ignore[arg-type]
    with pytest.raises(MoneyError):
        format_amount(12.5)  # type: ignore[arg-type]


def test_format_for_uses_currency_conventions():
    assert format_for("JPY", 1250) == "\u00a51,250"
    assert format_for("USD", 1250) == "$12.50"
    # KWD has 3 decimals and a symbol of its own; check the numeric part.
    assert format_for("KWD", 12345).endswith("12.345")
    assert format_for("KWD", 12345, symbol=False) == "12.345 KWD"
    assert "USD" in format_for("USD", 1250, symbol=False)


# --------------------------------------------------------------------------- #
# allocation — the core exactness guarantee
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "total,count",
    [
        (100, 3), (1, 3), (2, 3), (7, 3), (99, 7), (100000, 7), (5, 9),
        (12345, 11), (0, 4), (-100, 3), (-7, 3), (1, 2), (100, 100),
    ],
)
def test_split_evenly_always_conserves_total(total, count):
    parts = split_evenly(total, count)
    assert len(parts) == count
    assert sum(parts) == total


def test_split_evenly_100_across_three_gives_the_expected_residue():
    assert sorted(split_evenly(10000, 3), reverse=True) == [3334, 3333, 3333]


def test_split_evenly_tiny_amount_goes_to_the_lowest_index():
    parts = split_evenly(7, 3)
    assert parts == [3, 2, 2]
    assert sum(parts) == 7


def test_split_evenly_rejects_zero_count():
    with pytest.raises(MoneyError):
        split_evenly(100, 0)


def test_allocate_respects_weights():
    assert allocate(100, [2, 1, 1]) == [50, 25, 25]
    assert allocate(100, [1, 0, 0]) == [100, 0, 0]


def test_allocate_sums_exactly_for_awkward_weights():
    parts = allocate(10000, [7, 11, 13, 17, 19])
    assert sum(parts) == 10000


@pytest.mark.parametrize(
    "total,weights",
    [
        (10000, [1, 1, 1]), (5, [1, 1, 1]), (1, [7, 11]), (999, [1, 2, 3, 4]),
        (0, [1, 1]), (-10000, [1, 1, 1]), (100, [Fraction(1, 3), Fraction(2, 3)]),
        (100, ["1/3", "2/3"]), (100, [0.1, 0.2, 0.7]),
    ],
)
def test_allocate_conserves_money(total, weights):
    parts = allocate(total, weights)
    assert sum(parts) == total
    assert len(parts) == len(weights)


def test_allocate_gives_zero_weight_no_money_and_no_rounding_unit():
    parts = allocate(10000, [1, 1, 0])
    assert parts[2] == 0
    assert sum(parts) == 10000


def test_allocate_is_deterministic():
    first = allocate(10000, [1, 1, 1, 1, 1, 1, 1])
    for _ in range(20):
        assert allocate(10000, [1, 1, 1, 1, 1, 1, 1]) == first


def test_allocate_negative_total_gives_negative_parts():
    parts = allocate(-100, [1, 1])
    assert parts == [-50, -50]


def test_allocate_rejects_zero_weight_sum():
    with pytest.raises(MoneyError):
        allocate(100, [0, 0])


def test_allocate_rejects_negative_weight():
    with pytest.raises(MoneyError):
        allocate(100, [1, -1])


def test_allocate_rejects_empty_weights():
    with pytest.raises(MoneyError):
        allocate(100, [])


def test_allocate_rejects_non_int_total():
    with pytest.raises(MoneyError):
        allocate(1.5, [1, 1])  # type: ignore[arg-type]


def test_allocate_rejects_boolean_weight():
    with pytest.raises(MoneyError):
        allocate(100, [True, 1])


def test_allocate_rejects_garbage_weight():
    with pytest.raises(MoneyError):
        allocate(100, ["abc"])


def test_allocate_zero_total_returns_zeros():
    assert allocate(0, [1, 2, 3]) == [0, 0, 0]


# --------------------------------------------------------------------------- #
# percent parsing
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [("50", Fraction(50)), ("33.33", Fraction(3333, 100)), ("0", Fraction(0)),
     ("100", Fraction(100)), ("12.5%", Fraction(25, 2))],
)
def test_parse_percent(text, expected):
    assert parse_percent(text) == expected


def test_parse_percent_is_exact_for_thirds():
    # 33.33 + 33.33 + 33.34 == exactly 100 as fractions, not as floats.
    total = parse_percent("33.33") + parse_percent("33.33") + parse_percent("33.34")
    assert total == 100


def test_parse_percent_rejects_negative():
    with pytest.raises(MoneyError):
        parse_percent("-5")


def test_parse_percent_rejects_float_input():
    with pytest.raises(MoneyError):
        parse_percent(33.33)  # type: ignore[arg-type]


def test_parse_percent_rejects_junk():
    with pytest.raises(MoneyError):
        parse_percent("abc")
    with pytest.raises(MoneyError):
        parse_percent("")


# --------------------------------------------------------------------------- #
# sums
# --------------------------------------------------------------------------- #


def test_sum_minor():
    assert sum_minor([1, 2, 3]) == 6
    assert sum_minor([]) == 0
    assert sum_minor([-5, 5]) == 0


def test_sum_minor_rejects_floats():
    with pytest.raises(MoneyError):
        sum_minor([1, 2.5])  # type: ignore[list-item]
    with pytest.raises(MoneyError):
        sum_minor([True])  # type: ignore[list-item]


def test_float_rounding_would_lose_money_but_allocation_does_not():
    """The motivating bug: 100.00 split three ways by rounding loses a cent."""
    naive_each = round(10000 / 3)          # 3333
    assert naive_each * 3 != 10000         # 9999 — a cent vanished
    assert sum(split_evenly(10000, 3)) == 10000  # allocated, nothing lost
