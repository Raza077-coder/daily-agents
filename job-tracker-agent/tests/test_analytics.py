"""Tests for :mod:`jobtracker.analytics` — the cohort-aware funnel maths.

These are the tests that matter most: they pin the two rules that keep a job
hunt's numbers honest (furthest-rung tracking and maturity windows).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jobtracker.analytics import (
    STAGE_MATURITY_DAYS,
    STALL_DAYS,
    aging_rows,
    funnel,
    funnel_rows,
    salary_summary,
    source_breakdown,
    stalled,
    summary,
    time_in_stage,
    weekly_activity,
)
from jobtracker.models import SOURCES, STAGES, STAGE_INDEX, Event
from jobtracker.store import Vault

from conftest import TODAY, TODAY_STR, applied_app, make_app


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------
# Funnel shape
# --------------------------------------------------------------------------


def test_funnel_rows_cover_every_stage_in_order(vault):
    vault.add(applied_app("a", days_ago(40)))
    rows = funnel_rows(vault, TODAY)
    assert [r["stage"] for r in rows] == list(STAGES)


def test_empty_vault_funnel_reports_zero_without_dividing_by_zero(vault):
    data = funnel(vault, TODAY)
    assert data["submitted"] == 0
    assert data["response_rate"] == 0.0
    assert all(row["rate"] == 0.0 for row in data["rows"])


def test_wishlist_items_are_excluded_from_every_rate(vault):
    """They were never in the funnel."""
    vault.add(make_app("wish"))
    data = funnel(vault, TODAY)
    assert data["submitted"] == 0
    assert data["wishlist"] == 1


def test_funnel_counts_a_submitted_application(vault):
    vault.add(applied_app("a", days_ago(40)))
    data = funnel(vault, TODAY)
    assert data["submitted"] == 1
    assert data["rows"][0]["reached"] == 1


def test_furthest_rung_is_used_so_a_late_rejection_still_counts(vault):
    """The headline correctness rule of this whole module."""
    app = applied_app("cobalt", days_ago(44))
    app.events.append(Event(on=days_ago(18), kind="moved", to_status="onsite"))
    app.status = "rejected"
    app.closed_on = days_ago(9)
    app.events.append(
        Event(on=days_ago(9), kind="closed", from_status="onsite", to_status="rejected")
    )
    app.validate()
    vault.add(app)

    data = funnel(vault, TODAY)
    by_stage = {row["stage"]: row for row in data["rows"]}
    assert by_stage["onsite"]["reached"] == 1
    assert by_stage["offer"]["reached"] == 0
    assert data["submitted"] == 1


def test_a_young_application_is_not_counted_as_a_failure(vault):
    """Only mature applications enter the denominator."""
    vault.add(applied_app("fresh", days_ago(3)))
    data = funnel(vault, TODAY)
    applied_row = data["rows"][0]
    assert applied_row["denominator"] == 1  # `applied` is always the base
    screen_row = data["rows"][STAGE_INDEX["screen"]]
    assert screen_row["denominator"] == 0
    assert screen_row["rate"] == 0.0
    assert data["response_denominator"] == 0


def test_a_mature_application_enters_the_denominator(vault):
    vault.add(applied_app("old", days_ago(STAGE_MATURITY_DAYS["applied"] + 5)))
    data = funnel(vault, TODAY)
    assert data["response_denominator"] == 1


def test_step_rate_compares_adjacent_rungs(vault):
    for i in range(4):
        vault.add(applied_app(f"s{i}", days_ago(60)))
    screened = vault.get("s0")
    screened.status = "screen"
    screened.events.append(Event(on=days_ago(50), kind="moved", to_status="screen"))
    screened.validate()

    data = funnel(vault, TODAY)
    screen_row = data["rows"][STAGE_INDEX["screen"]]
    assert screen_row["reached"] == 1
    assert screen_row["step_rate"] == pytest.approx(25.0, abs=0.1)


def test_response_rate_is_reported_against_mature_applications_only(vault):
    answered = applied_app("answered", days_ago(40))
    answered.status = "screen"
    answered.events.append(Event(on=days_ago(35), kind="moved", to_status="screen"))
    answered.validate()
    vault.add(answered)
    vault.add(applied_app("fresh", days_ago(2)))  # too young to judge

    data = funnel(vault, TODAY)
    assert data["responded"] == 1
    assert data["response_denominator"] == 1
    assert data["response_rate"] == pytest.approx(100.0, abs=0.1)


def test_headline_counts_are_consistent(vault):
    vault.add(applied_app("a", days_ago(40)))
    vault.add(make_app("w"))
    data = funnel(vault, TODAY)
    assert data["submitted"] == 1
    assert data["open"] == 1
    assert data["wishlist"] == 1
    assert data["closed"] == 0


# --------------------------------------------------------------------------
# Aging & stalls
# --------------------------------------------------------------------------


def test_aging_rows_only_include_open_applications(vault):
    vault.add(applied_app("open", days_ago(30)))
    closed = applied_app("closed", days_ago(30))
    closed.status = "rejected"
    closed.closed_on = days_ago(1)
    closed.validate()
    vault.add(closed)

    assert [r["id"] for r in aging_rows(vault, TODAY)] == ["open"]


def test_aging_rows_report_the_stall_flag_against_the_threshold(vault):
    vault.add(applied_app("quiet", days_ago(60)))
    row = aging_rows(vault, TODAY)[0]
    assert row["days_since_activity"] == 60
    assert row["stall_threshold"] == STALL_DAYS["applied"]
    assert row["stalled"] is True


def test_a_recently_touched_application_is_not_stalled(vault):
    app = applied_app("busy", days_ago(60))
    app.events.append(Event(on=days_ago(2), kind="followup"))
    app.validate()
    vault.add(app)
    assert stalled(vault, TODAY) == []


def test_aging_rows_are_sorted_quietest_first(vault):
    vault.add(applied_app("recent", days_ago(20)))
    vault.add(applied_app("ancient", days_ago(90)))
    assert [r["id"] for r in aging_rows(vault, TODAY)] == ["ancient", "recent"]


def test_stalled_is_a_subset_of_aging(vault):
    vault.add(applied_app("stuck", days_ago(90)))
    vault.add(applied_app("fine", days_ago(1)))
    assert [r["id"] for r in stalled(vault, TODAY)] == ["stuck"]


# --------------------------------------------------------------------------
# Time in stage
# --------------------------------------------------------------------------


def test_time_in_stage_only_counts_completed_stretches(vault):
    """An application still waiting has not finished its time in that stage."""
    vault.add(applied_app("waiting", days_ago(5)))
    data = time_in_stage(vault, TODAY)
    assert data["applied"]["samples"] == 0
    assert data["applied"]["median_days"] is None


def test_time_in_stage_measures_a_closed_stretch(vault):
    app = applied_app("moved", days_ago(40))
    app.events.append(Event(on=days_ago(30), kind="moved", to_status="screen"))
    app.status = "screen"
    app.closure = None
    app.validate()
    vault.add(app)

    data = time_in_stage(vault, TODAY)
    assert data["applied"]["samples"] == 1
    assert data["applied"]["median_days"] == 10


def test_time_in_stage_ends_on_the_close_date(vault):
    app = applied_app("rejected", days_ago(40))
    app.status = "rejected"
    app.closed_on = days_ago(25)
    app.events.append(Event(on=days_ago(25), kind="closed", to_status="rejected"))
    app.validate()
    vault.add(app)

    data = time_in_stage(vault, TODAY)
    assert data["applied"]["median_days"] == 15


def test_time_in_stage_reports_every_stage_key(vault):
    assert set(time_in_stage(vault, TODAY)) == set(STAGES)


# --------------------------------------------------------------------------
# Sources & salary
# --------------------------------------------------------------------------


def test_source_breakdown_excludes_unsubmitted_rows(vault):
    vault.add(make_app("wish", source="referral"))
    assert source_breakdown(vault, TODAY) == []


def test_source_breakdown_ranks_by_interview_rate(vault):
    referral = applied_app("ref", days_ago(40), source="referral")
    referral.status = "interview"
    referral.events.append(Event(on=days_ago(30), kind="moved", to_status="interview"))
    referral.validate()
    vault.add(referral)
    vault.add(applied_app("board", days_ago(40), source="job_board"))

    rows = source_breakdown(vault, TODAY)
    assert rows[0]["source"] == "referral"
    assert rows[0]["interview_rate"] > rows[1]["interview_rate"]


def test_source_breakdown_labels_every_row(vault):
    vault.add(applied_app("a", days_ago(40), source="linkedin"))
    row = source_breakdown(vault, TODAY)[0]
    assert row["label"] == "LinkedIn"


def test_salary_summary_never_mixes_currencies(vault):
    vault.add(applied_app("usd", days_ago(30), currency="USD", salary_min="100000"))
    vault.add(
        applied_app("eur", days_ago(30), currency="EUR", salary_min="90000")
    )
    data = salary_summary(vault)
    assert set(data["currencies"]) == {"USD", "EUR"}
    assert data["currencies"]["USD"]["min"] == 10_000_000
    assert data["currencies"]["EUR"]["min"] == 9_000_000


def test_salary_summary_ignores_entries_without_a_range(vault):
    vault.add(applied_app("a", days_ago(30)))
    assert salary_summary(vault) == {"currencies": {}, "entries": 0}


def test_salary_summary_reports_a_median_range(vault):
    vault.add(applied_app("a", days_ago(30), currency="USD", salary_min="100000", salary_max="120000"))
    vault.add(applied_app("b", days_ago(30), currency="USD", salary_min="140000", salary_max="160000"))
    data = salary_summary(vault)["currencies"]["USD"]
    assert data["min"] == 10_000_000
    assert data["max"] == 16_000_000
    assert data["count"] == 2


# --------------------------------------------------------------------------
# Weekly throughput & streaks
# --------------------------------------------------------------------------


def test_weekly_activity_returns_the_requested_number_of_buckets(vault):
    rows = weekly_activity(vault, TODAY, weeks=8)
    assert len(rows) == 8
    assert rows[-1]["week_end"] >= TODAY_STR


def test_weekly_activity_counts_applications_in_their_week(vault):
    vault.add(applied_app("a", days_ago(3)))
    rows = weekly_activity(vault, TODAY, weeks=4)
    assert sum(r["applied"] for r in rows) == 1


def test_weekly_activity_is_clamped_to_a_sane_range(vault):
    assert len(weekly_activity(vault, TODAY, weeks=0)) == 1
    assert len(weekly_activity(vault, TODAY, weeks=999)) == 52


def test_weekly_activity_labels_each_bucket(vault):
    row = weekly_activity(vault, TODAY, weeks=2)[0]
    assert row["label"]
    assert row["iso_week"].count("-W") == 1


# --------------------------------------------------------------------------
# Summary bundle
# --------------------------------------------------------------------------


def test_summary_bundle_has_every_documented_key(vault):
    data = summary(vault, TODAY)
    for key in (
        "today",
        "totals",
        "funnel",
        "aging",
        "stalled",
        "time_in_stage",
        "sources",
        "salary",
        "weekly",
        "streak",
        "status_counts",
    ):
        assert key in data, f"missing key: {key}"


def test_summary_totals_partition_the_vault(vault):
    vault.add(applied_app("a", days_ago(30)))
    vault.add(make_app("w"))
    closed = applied_app("c", days_ago(30))
    closed.status = "withdrawn"
    closed.closed_on = days_ago(5)
    closed.validate()
    vault.add(closed)

    totals = summary(vault, TODAY)["totals"]
    assert totals["applications"] == 3
    assert totals["submitted"] == 2
    assert totals["open"] == 1
    assert totals["wishlist"] == 1
    assert totals["closed"] == 1


def test_status_counts_cover_every_status_key(vault):
    vault.add(applied_app("a", days_ago(1)))
    counts = summary(vault, TODAY)["status_counts"]
    assert counts["applied"] == 1
    assert counts["offer"] == 0


def test_summary_is_deterministic_for_a_fixed_date(vault):
    vault.add(applied_app("a", days_ago(30)))
    assert summary(vault, TODAY) == summary(vault, TODAY)


def test_summary_today_field_echoes_the_reference_date(vault):
    assert summary(vault, TODAY)["today"] == TODAY_STR
