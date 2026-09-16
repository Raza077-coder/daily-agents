"""Tests for the ledger: balances, the zero-sum invariant, and the audit."""

from __future__ import annotations

import pytest

from splitkit import ledger
from splitkit.errors import SettleError
from splitkit.models import Group
from splitkit.storage import new_group


def _group(currency: str = "USD") -> Group:
    g = new_group("Test", ["Ali", "Sara", "Bilal"], currency=currency)
    return g


# --------------------------------------------------------------------------- #
# totals
# --------------------------------------------------------------------------- #


def test_paid_totals_count_what_each_person_fronted():
    g = _group()
    g.add_expense("A", 1000, "ali")
    g.add_expense("B", 500, "ali")
    g.add_expense("C", 300, "sara")
    paid = ledger.paid_totals(g)
    assert paid == {"ali": 1500, "sara": 300, "bilal": 0}


def test_owed_totals_sum_to_the_group_total():
    g = _group()
    g.add_expense("A", 900, "ali")
    g.add_expense("B", 300, "sara")
    owed = ledger.owed_totals(g)
    assert sum(owed.values()) == g.total_minor == 1200


def test_owed_totals_use_the_split_not_the_payer():
    g = _group()
    g.add_expense("Steak", 3000, "ali", {"mode": "exact", "amounts": {"sara": 3000}})
    owed = ledger.owed_totals(g)
    assert owed["sara"] == 3000
    assert owed["ali"] == 0


def test_settled_totals_reflect_recorded_repayments():
    g = _group()
    g.add_settlement("bilal", "ali", 2000)
    net = ledger.settled_totals(g)
    assert net["bilal"] == 2000
    assert net["ali"] == -2000


# --------------------------------------------------------------------------- #
# balances and the invariant
# --------------------------------------------------------------------------- #


def test_balances_sum_to_zero_for_a_simple_group():
    g = _group()
    g.add_expense("Dinner", 9000, "ali")
    net = ledger.balances(g)
    assert sum(net.values()) == 0
    assert net["ali"] == 6000       # paid 9000, owed 3000
    assert net["sara"] == -3000
    assert net["bilal"] == -3000


def test_balances_are_zero_with_no_expenses():
    assert ledger.balances(_group()) == {"ali": 0, "sara": 0, "bilal": 0}


def test_balances_sum_to_zero_when_the_amount_does_not_divide_evenly():
    g = _group()
    g.add_expense("Indivisible", 10000, "ali")
    net = ledger.balances(g)
    assert sum(net.values()) == 0


def test_balances_sum_to_zero_with_many_awkward_expenses(demo_group):
    net = ledger.balances(demo_group)
    assert sum(net.values()) == 0


def test_balances_sum_to_zero_across_many_random_amounts():
    g = _group()
    for i in range(1, 40):
        g.add_expense(f"E{i}", i * 7 + 1, ["ali", "sara", "bilal"][i % 3])
    net = ledger.balances(g)
    assert sum(net.values()) == 0


def test_settlement_reduces_a_balance():
    g = _group()
    g.add_expense("Dinner", 9000, "ali")
    g.add_settlement("sara", "ali", 3000)
    net = ledger.balances(g)
    assert net["sara"] == 0
    assert net["ali"] == 3000
    assert sum(net.values()) == 0


def test_recording_settlements_can_clear_the_book_entirely():
    g = _group()
    g.add_expense("Dinner", 9000, "ali")
    g.add_settlement("sara", "ali", 3000)
    g.add_settlement("bilal", "ali", 3000)
    net = ledger.balances(g)
    assert all(v == 0 for v in net.values())
    creditors, debtors = ledger.creditors_and_debtors(g)
    assert creditors == [] and debtors == []


def test_health_reports_settled_when_everyone_is_square():
    g = _group()
    g.add_expense("Dinner", 9000, "ali")
    g.add_settlement("sara", "ali", 3000)
    g.add_settlement("bilal", "ali", 3000)
    h = ledger.health(g)
    assert h["is_settled"] is True
    assert h["is_balanced"] is True
    assert h["outstanding_minor"] == 0


def test_zero_sum_invariant_is_checked_and_raises_when_broken(monkeypatch):
    """A deliberately corrupted ledger must fail loudly, not report quietly."""
    g = _group()
    g.add_expense("Dinner", 9000, "ali")
    original = ledger.owed_totals
    monkeypatch.setattr(
        ledger, "owed_totals", lambda grp: {"ali": 0, "sara": 0, "bilal": 0}
    )
    with pytest.raises(SettleError) as err:
        ledger.balances(g)
    assert "does not balance" in str(err.value)
    monkeypatch.setattr(ledger, "owed_totals", original)


def test_balances_can_skip_the_invariant_check(monkeypatch):
    g = _group()
    g.add_expense("Dinner", 9000, "ali")
    monkeypatch.setattr(ledger, "owed_totals", lambda grp: {"ali": 0, "sara": 0, "bilal": 0})
    net = ledger.balances(g, check_invariant=False)
    assert net["ali"] == 9000  # nonsense, but no exception when unchecked


# --------------------------------------------------------------------------- #
# creditors / debtors
# --------------------------------------------------------------------------- #


def test_creditors_and_debtors_split_correctly(demo_group):
    creditors, debtors = ledger.creditors_and_debtors(demo_group)
    assert all(v > 0 for _, v in creditors)
    assert all(v < 0 for _, v in debtors)
    assert sum(v for _, v in creditors) == -sum(v for _, v in debtors)


def test_creditors_are_sorted_largest_first():
    g = _group()
    g.add_expense("A", 1000, "ali")
    g.add_expense("B", 5000, "sara")
    creditors, _ = ledger.creditors_and_debtors(g)
    amounts = [v for _, v in creditors]
    assert amounts == sorted(amounts, reverse=True)


def test_debtors_are_sorted_most_negative_first():
    g = _group()
    g.add_expense("A", 1000, "ali")
    g.add_expense("B", 5000, "sara")
    _, debtors = ledger.creditors_and_debtors(g)
    amounts = [v for _, v in debtors]
    assert amounts == sorted(amounts)


def test_equal_balances_tie_break_alphabetically():
    g = new_group("T", ["Zed", "Abe", "Mia"])
    g.add_expense("A", 300, "zed")
    creditors, _ = ledger.creditors_and_debtors(g)
    assert [m for m, _ in creditors] == ["zed"]


# --------------------------------------------------------------------------- #
# breakdowns
# --------------------------------------------------------------------------- #


def test_breakdowns_resolve_every_expense_exactly(demo_group):
    for bd in ledger.breakdowns(demo_group):
        assert sum(bd.shares.values()) == bd.expense.amount


def test_breakdown_exposes_the_readable_split_rule():
    g = _group()
    g.add_expense("A", 1000, "ali", {"mode": "shares", "shares": {"ali": 1, "sara": 1}})
    bd = ledger.breakdowns(g)[0]
    payload = bd.to_dict("USD")
    assert "shares" in payload["split_explained"]
    assert payload["amount_display"] == "10.00"


def test_breakdown_count_matches_expense_count(demo_group):
    assert len(ledger.breakdowns(demo_group)) == len(demo_group.expenses)


# --------------------------------------------------------------------------- #
# categories and matrices
# --------------------------------------------------------------------------- #


def test_category_totals_group_and_sort_by_size(demo_group):
    cats = ledger.category_totals(demo_group)
    values = list(cats.values())
    assert sum(values) == demo_group.total_minor
    assert values == sorted(values, reverse=True)


def test_member_spend_matrix_uses_share_not_payment(demo_group):
    matrix = ledger.member_spend_matrix(demo_group)
    for member_id, cats in matrix.items():
        assert sum(cats.values()) == ledger.owed_totals(demo_group)[member_id]


def test_top_expenses_are_ordered_by_size_then_id(demo_group):
    top = ledger.top_expenses(demo_group, limit=3)
    assert len(top) == 3
    amounts = [e.amount for e in top]
    assert amounts == sorted(amounts, reverse=True)
    ids = [e.id for e in top if e.amount == top[0].amount]
    assert ids == sorted(ids)


def test_top_expenses_limit_zero_is_empty(demo_group):
    assert ledger.top_expenses(demo_group, limit=0) == []


def test_health_counts_and_display(demo_group):
    h = ledger.health(demo_group)
    assert h["member_count"] == 3
    assert h["expense_count"] == len(demo_group.expenses)
    assert h["total_display"] == f"{h['total_minor'] // 100}.{h['total_minor'] % 100:02d}"
    assert h["is_balanced"] is True
    assert set(h["categories"]) == set(ledger.category_totals(demo_group))


def test_health_average_expense_uses_integer_division(demo_group):
    h = ledger.health(demo_group)
    expected = demo_group.total_minor // len(demo_group.expenses)
    assert h["average_expense_minor"] == expected
    assert isinstance(h["average_expense_minor"], int)


def test_health_on_an_empty_group_is_graceful():
    h = ledger.health(_group())
    assert h["expense_count"] == 0
    assert h["total_minor"] == 0
    assert h["largest_expense_minor"] == 0
    assert h["average_expense_minor"] == 0
    assert h["is_settled"] is True


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


def test_audit_flags_a_single_participant_on_a_group_expense():
    g = _group()
    g.add_expense("Solo treat", 1000, "ali", {"mode": "exact", "amounts": {"ali": 1000}})
    findings = ledger.audit(g)
    assert any("charged entirely to" in f["message"] for f in findings)


def test_audit_flags_suspiciously_tiny_amounts():
    g = _group()
    g.add_expense("Tiny", 1, "ali")
    findings = ledger.audit(g)
    assert any("decimal-point slip" in f["message"] for f in findings)


def test_audit_is_silent_for_a_healthy_group(demo_group):
    findings = [f for f in ledger.audit(demo_group) if f["level"] == "warning"]
    assert findings == []


def test_audit_returns_structured_findings():
    g = _group()
    g.add_expense("Tiny", 1, "ali")
    for f in ledger.audit(g):
        assert set(f) >= {"level", "expense", "message"}
        assert f["level"] in ("info", "warning")
