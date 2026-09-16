"""Tests for the six split modes and their validation rules."""

from __future__ import annotations

import pytest

from splitkit.errors import SplitError, UnknownMemberError
from splitkit.splits import MODES, MODE_DESCRIPTIONS, describe, participants_of, resolve

MEMBERS = ["ali", "sara", "bilal"]


# --------------------------------------------------------------------------- #
# mode registry
# --------------------------------------------------------------------------- #


def test_all_six_modes_exist():
    assert set(MODES) == {"equal", "exact", "shares", "percent", "itemized", "adjustment"}
    assert len(MODES) == 6


def test_every_mode_has_a_description():
    for mode in MODES:
        assert mode in MODE_DESCRIPTIONS
        assert len(MODE_DESCRIPTIONS[mode]) > 20


def test_unknown_mode_is_rejected_with_the_valid_list():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "magic"}, 1000, MEMBERS)
    assert "magic" in str(err.value)
    assert "equal" in str(err.value)


def test_mode_defaults_to_equal_when_absent():
    assert resolve({}, 900, MEMBERS) == {"ali": 300, "sara": 300, "bilal": 300}


def test_split_must_be_a_mapping():
    with pytest.raises(SplitError):
        resolve(["equal"], 1000, MEMBERS)  # type: ignore[arg-type]


def test_zero_amount_resolves_to_nobody():
    assert resolve({"mode": "equal"}, 0, MEMBERS) == {}


# --------------------------------------------------------------------------- #
# equal
# --------------------------------------------------------------------------- #


def test_equal_splits_across_everyone_by_default():
    assert resolve({"mode": "equal"}, 1200, MEMBERS) == {"ali": 400, "sara": 400, "bilal": 400}


def test_equal_allocates_the_residue_exactly():
    parts = resolve({"mode": "equal"}, 10000, MEMBERS)
    assert sum(parts.values()) == 10000
    assert sorted(parts.values(), reverse=True) == [3334, 3333, 3333]


def test_equal_honours_participants_subset():
    parts = resolve({"mode": "equal", "participants": ["ali", "sara"]}, 1000, MEMBERS)
    assert parts == {"ali": 500, "sara": 500}
    assert "bilal" not in parts


def test_equal_single_participant_takes_all():
    assert resolve({"mode": "equal", "participants": ["ali"]}, 1234, MEMBERS) == {"ali": 1234}


def test_participants_rejects_a_bare_string():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "equal", "participants": "ali"}, 1000, MEMBERS)
    assert "list" in str(err.value)


def test_participants_rejects_unknown_member():
    with pytest.raises(UnknownMemberError) as err:
        resolve({"mode": "equal", "participants": ["nobody"]}, 1000, MEMBERS)
    assert "nobody" in str(err.value)


def test_participants_rejects_duplicates():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "equal", "participants": ["ali", "ali"]}, 1000, MEMBERS)
    assert "duplicate" in str(err.value)


def test_participants_empty_list_is_refused():
    with pytest.raises(SplitError):
        resolve({"mode": "equal", "participants": []}, 1000, MEMBERS)


def test_participants_of_defaults_to_all_members():
    assert participants_of({}, MEMBERS) == MEMBERS


# --------------------------------------------------------------------------- #
# exact
# --------------------------------------------------------------------------- #


def test_exact_mode_uses_the_given_amounts():
    parts = resolve(
        {"mode": "exact", "amounts": {"ali": 1200, "sara": 600, "bilal": 600}}, 2400, MEMBERS
    )
    assert parts == {"ali": 1200, "sara": 600, "bilal": 600}


def test_exact_mode_parses_string_amounts():
    parts = resolve(
        {"mode": "exact", "amounts": {"ali": "12.00", "sara": "6.00"}}, 1800, ["ali", "sara"]
    )
    assert parts == {"ali": 1200, "sara": 600}


def test_exact_mode_rejects_a_mismatch_and_says_by_how_much():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "exact", "amounts": {"ali": 1200, "sara": 600}}, 2000, ["ali", "sara"])
    message = str(err.value)
    assert "18.00" in message and "20.00" in message
    assert "+2.00" in message or "2.00" in message


def test_exact_mode_names_the_unknown_member():
    with pytest.raises(UnknownMemberError):
        resolve({"mode": "exact", "amounts": {"ghost": 1000}}, 1000, MEMBERS)


def test_exact_mode_needs_the_amounts_key():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "exact"}, 1000, MEMBERS)
    assert "amounts" in str(err.value)


def test_exact_mode_rejects_float_amounts():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "exact", "amounts": {"ali": 12.5}}, 1250, MEMBERS)
    assert "float" in str(err.value).lower()


def test_exact_mode_requires_all_three_to_balance_zubairy():
    # The classic typo: two people listed on a three-person expense.
    with pytest.raises(SplitError):
        resolve({"mode": "exact", "amounts": {"ali": 500, "sara": 500}}, 1500, MEMBERS)


# --------------------------------------------------------------------------- #
# shares
# --------------------------------------------------------------------------- #


def test_shares_mode_weights_correctly():
    parts = resolve(
        {"mode": "shares", "shares": {"ali": 2, "sara": 1, "bilal": 1}}, 9240, MEMBERS
    )
    assert parts == {"ali": 4620, "sara": 2310, "bilal": 2310}
    assert sum(parts.values()) == 9240


def test_shares_mode_handles_indivisible_weights_exactly():
    parts = resolve({"mode": "shares", "shares": {"ali": 1, "sara": 1, "bilal": 1}}, 10000, MEMBERS)
    assert sum(parts.values()) == 10000


def test_shares_mode_accepts_string_weights():
    parts = resolve({"mode": "shares", "shares": {"ali": "3", "sara": "1"}}, 1000, ["ali", "sara"])
    assert parts == {"ali": 750, "sara": 250}


def test_shares_mode_accepts_fractional_weights():
    parts = resolve(
        {"mode": "shares", "shares": {"ali": "1.5", "sara": "0.5"}}, 1000, ["ali", "sara"]
    )
    assert parts == {"ali": 750, "sara": 250}


def test_shares_mode_rejects_zero_total_weight():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "shares", "shares": {"ali": 0, "sara": 0}}, 1000, ["ali", "sara"])
    assert "zero" in str(err.value)


def test_shares_mode_rejects_negative_weight():
    with pytest.raises(SplitError):
        resolve({"mode": "shares", "shares": {"ali": -1, "sara": 2}}, 1000, ["ali", "sara"])


def test_shares_mode_needs_the_shares_key():
    with pytest.raises(SplitError):
        resolve({"mode": "shares"}, 1000, MEMBERS)


def test_shares_mode_rejects_junk_weight():
    with pytest.raises(SplitError):
        resolve({"mode": "shares", "shares": {"ali": "abc"}}, 1000, MEMBERS)


def test_zero_share_member_gets_nothing():
    parts = resolve({"mode": "shares", "shares": {"ali": 1, "sara": 1, "bilal": 0}}, 1000, MEMBERS)
    assert parts.get("bilal") is None  # zero shares are omitted


# --------------------------------------------------------------------------- #
# percent
# --------------------------------------------------------------------------- #


def test_percent_mode_splits_by_percentage():
    parts = resolve(
        {"mode": "percent", "percents": {"ali": "60", "sara": "40"}}, 1000, ["ali", "sara"]
    )
    assert parts == {"ali": 600, "sara": 400}


def test_percent_mode_requires_exactly_one_hundred():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "percent", "percents": {"ali": "60", "sara": "30"}}, 1000, ["ali", "sara"])
    assert "90" in str(err.value) and "100" in str(err.value)


def test_percent_mode_is_exact_for_thirds():
    parts = resolve(
        {"mode": "percent", "percents": {"ali": "33.33", "sara": "33.33", "bilal": "33.34"}},
        10000, MEMBERS,
    )
    assert sum(parts.values()) == 10000
    assert parts["bilal"] == 3334


def test_percent_mode_rejects_over_one_hundred():
    with pytest.raises(SplitError):
        resolve({"mode": "percent", "percents": {"ali": "150"}}, 1000, MEMBERS)


def test_percent_mode_needs_the_percents_key():
    with pytest.raises(SplitError):
        resolve({"mode": "percent"}, 1000, MEMBERS)


def test_percent_mode_rejects_negative():
    with pytest.raises(SplitError):
        resolve({"mode": "percent", "percents": {"ali": "-10", "sara": "110"}}, 1000, ["ali", "sara"])


def test_percent_mode_accepts_percent_sign():
    parts = resolve(
        {"mode": "percent", "percents": {"ali": "25%", "sara": "75%"}}, 1000, ["ali", "sara"]
    )
    assert parts == {"ali": 250, "sara": 750}


# --------------------------------------------------------------------------- #
# itemized
# --------------------------------------------------------------------------- #


def test_itemized_assigns_each_item_to_its_participants():
    parts = resolve(
        {
            "mode": "itemized",
            "items": [
                {"label": "Steak", "amount": 3000, "participants": ["ali"]},
                {"label": "Salad", "amount": 1000, "participants": ["sara"]},
            ],
        },
        4000, MEMBERS,
    )
    assert parts == {"ali": 3000, "sara": 1000}


def test_itemized_shared_item_splits_across_its_participants():
    parts = resolve(
        {
            "mode": "itemized",
            "items": [{"label": "Pizza", "amount": 1000, "participants": ["ali", "sara"]}],
        },
        1000, ["ali", "sara"],
    )
    assert parts == {"ali": 500, "sara": 500}


def test_itemized_item_without_participants_goes_to_everyone():
    parts = resolve(
        {"mode": "itemized", "items": [{"label": "Bread", "amount": 900}]}, 900, MEMBERS
    )
    assert parts == {"ali": 300, "sara": 300, "bilal": 300}


def test_itemized_spreads_tax_proportionally_by_default():
    parts = resolve(
        {
            "mode": "itemized",
            "items": [
                {"label": "A", "amount": 3000, "participants": ["ali"]},
                {"label": "B", "amount": 1000, "participants": ["sara"]},
            ],
            "tax": 400,
        },
        4400, MEMBERS,
    )
    assert parts == {"ali": 3300, "sara": 1100}
    assert sum(parts.values()) == 4400


def test_itemized_spreads_tip_equally_when_asked():
    parts = resolve(
        {
            "mode": "itemized",
            "items": [
                {"label": "A", "amount": 3000, "participants": ["ali", "sara"]},
            ],
            "tip": 200,
            "tip_mode": "equal",
        },
        3200, ["ali", "sara"],
    )
    assert parts == {"ali": 1600, "sara": 1600}


def test_itemized_tax_and_tip_together_reconcile_exactly():
    parts = resolve(
        {
            "mode": "itemized",
            "items": [
                {"label": "A", "amount": 3333, "participants": ["ali", "sara", "bilal"]},
            ],
            "tax": 333,
            "tip": 334,
        },
        4000, MEMBERS,
    )
    assert sum(parts.values()) == 4000


def test_itemized_rejects_a_total_mismatch():
    with pytest.raises(SplitError) as err:
        resolve(
            {"mode": "itemized", "items": [{"label": "A", "amount": 3000}]}, 4000, MEMBERS
        )
    assert "receipt total" in str(err.value)


def test_itemized_needs_items():
    with pytest.raises(SplitError):
        resolve({"mode": "itemized", "items": []}, 1000, MEMBERS)
    with pytest.raises(SplitError):
        resolve({"mode": "itemized"}, 1000, MEMBERS)


def test_itemized_rejects_unknown_item_participant():
    with pytest.raises(UnknownMemberError):
        resolve(
            {"mode": "itemized", "items": [{"label": "A", "amount": 1000, "participants": ["ghost"]}]},
            1000, MEMBERS,
        )


def test_itemized_rejects_negative_item_amount():
    with pytest.raises(SplitError):
        resolve(
            {"mode": "itemized", "items": [{"label": "A", "amount": -100}]}, -100, MEMBERS
        )


def test_itemized_rejects_bad_spread_mode():
    with pytest.raises(SplitError) as err:
        resolve(
            {
                "mode": "itemized",
                "items": [{"label": "A", "amount": 1000, "participants": ["ali"]}],
                "tax": 100,
                "tax_mode": "sideways",
            },
            1100, MEMBERS,
        )
    assert "tax_mode" in str(err.value)


def test_itemized_rejects_float_item_amount():
    with pytest.raises(SplitError):
        resolve(
            {"mode": "itemized", "items": [{"label": "A", "amount": 10.5}]}, 1050, MEMBERS
        )


def test_itemized_item_must_be_an_object():
    with pytest.raises(SplitError):
        resolve({"mode": "itemized", "items": ["A:10"]}, 1000, MEMBERS)


# --------------------------------------------------------------------------- #
# adjustment
# --------------------------------------------------------------------------- #


def test_adjustment_starts_from_an_even_split():
    parts = resolve(
        {
            "mode": "adjustment",
            "adjustments": {"ali": 900, "sara": -900},
        },
        4500, MEMBERS,
    )
    # even base is 1500 each; ali +900 -> 2400, sara -900 -> 600
    assert parts == {"ali": 2400, "sara": 600, "bilal": 1500}
    assert sum(parts.values()) == 4500


def test_adjustment_must_net_to_zero():
    with pytest.raises(SplitError) as err:
        resolve({"mode": "adjustment", "adjustments": {"ali": 500}}, 3000, MEMBERS)
    assert "net to zero" in str(err.value)


def test_adjustment_refuses_to_push_a_share_negative():
    with pytest.raises(SplitError) as err:
        resolve(
            {"mode": "adjustment", "adjustments": {"ali": 5000, "sara": -5000}},
            3000, MEMBERS,
        )
    assert "below zero" in str(err.value)


def test_adjustment_rejects_adjusting_a_non_participant():
    with pytest.raises(SplitError) as err:
        resolve(
            {
                "mode": "adjustment",
                "participants": ["ali", "sara"],
                "adjustments": {"bilal": 100, "ali": -100},
            },
            2000, MEMBERS,
        )
    assert "participants" in str(err.value)


def test_adjustment_with_no_adjustments_is_an_even_split():
    assert resolve({"mode": "adjustment"}, 900, MEMBERS) == {"ali": 300, "sara": 300, "bilal": 300}


def test_adjustment_parses_string_amounts():
    parts = resolve(
        {"mode": "adjustment", "adjustments": {"ali": "5.00", "sara": "-5.00"}}, 3000, MEMBERS
    )
    assert parts["ali"] == 1500 and parts["sara"] == 500


# --------------------------------------------------------------------------- #
# invariants across all modes
# --------------------------------------------------------------------------- #


def _every_mode_specs():
    return [
        ({"mode": "equal"}, 10000, MEMBERS),
        ({"mode": "exact", "amounts": {"ali": 3334, "sara": 3333, "bilal": 3333}}, 10000, MEMBERS),
        ({"mode": "shares", "shares": {"ali": 1, "sara": 1, "bilal": 1}}, 10000, MEMBERS),
        ({"mode": "percent", "percents": {"ali": "33.33", "sara": "33.33", "bilal": "33.34"}}, 10000, MEMBERS),
        ({"mode": "itemized", "items": [{"label": "A", "amount": 10000}]}, 10000, MEMBERS),
        ({"mode": "adjustment", "adjustments": {"ali": 1, "sara": -1}}, 10000, MEMBERS),
    ]


@pytest.mark.parametrize("spec,amount,members", _every_mode_specs())
def test_every_mode_sums_exactly_to_the_expense(spec, amount, members):
    parts = resolve(spec, amount, members)
    assert sum(parts.values()) == amount


@pytest.mark.parametrize("spec,amount,members", _every_mode_specs())
def test_every_mode_is_deterministic(spec, amount, members):
    assert resolve(spec, amount, members) == resolve(spec, amount, members)


@pytest.mark.parametrize("spec,amount,members", _every_mode_specs())
def test_every_mode_omits_zero_shares(spec, amount, members):
    parts = resolve(spec, amount, members)
    assert all(v != 0 for v in parts.values())


@pytest.mark.parametrize("spec,amount,members", _every_mode_specs())
def test_every_mode_only_names_known_members(spec, amount, members):
    parts = resolve(spec, amount, members)
    assert set(parts).issubset(set(members))


@pytest.mark.parametrize("currency", ["USD", "JPY", "KWD"])
def test_currency_exponent_is_honoured_in_parsing(currency):
    spec = {"mode": "exact", "amounts": {"ali": "1", "sara": "1"}}
    amount = 2 * (10 ** {"USD": 2, "JPY": 0, "KWD": 3}[currency])
    parts = resolve(spec, amount, ["ali", "sara"], currency=currency)
    assert sum(parts.values()) == amount


# --------------------------------------------------------------------------- #
# describe()
# --------------------------------------------------------------------------- #


def test_describe_explains_each_mode():
    names = {"ali": "Ali", "sara": "Sara", "bilal": "Bilal"}
    lookup = lambda m: names.get(m, m)  # noqa: E731

    assert "evenly" in describe({"mode": "equal"}, lookup)
    assert "Ali" in describe({"mode": "equal", "participants": ["ali", "sara"]}, lookup)
    assert "exact" in describe({"mode": "exact", "amounts": {"ali": 100}}, lookup)
    assert "shares" in describe({"mode": "shares", "shares": {"ali": 2}}, lookup)
    assert "percentage" in describe({"mode": "percent", "percents": {"ali": "50"}}, lookup)
    assert "itemized" in describe(
        {"mode": "itemized", "items": [{"label": "A", "amount": 100, "participants": ["ali"]}], "tax": 10},
        lookup,
    )
    assert "adjusted" in describe({"mode": "adjustment", "adjustments": {"ali": 10}}, lookup)


def test_describe_handles_a_non_mapping_gracefully():
    assert describe("nonsense", lambda m: m) == "shared (unknown rule)"  # type: ignore[arg-type]


def test_describe_itemized_with_everyone():
    text = describe(
        {"mode": "itemized", "items": [{"label": "Bread", "amount": 500}]}, lambda m: m, "USD"
    )
    assert "everyone" in text
