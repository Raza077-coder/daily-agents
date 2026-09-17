"""Unit tests for :mod:`jobtracker.models` — parsing, validation, derived state."""

from __future__ import annotations

from datetime import date, datetime

import pytest

from jobtracker.models import (
    ACTIVE,
    CLOSED,
    STAGES,
    STATUSES,
    Application,
    Event,
    Interview,
    NotFoundError,
    Note,
    ValidationError,
    clean_text,
    format_money,
    format_range,
    make_id,
    one_of,
    parse_date,
    parse_money,
    parse_priority,
    slugify,
)

from conftest import TODAY, TODAY_STR, applied_app, make_app

# --------------------------------------------------------------------------
# parse_date
# --------------------------------------------------------------------------


def test_parse_date_accepts_iso_string():
    assert parse_date("2026-09-17") == date(2026, 9, 17)


def test_parse_date_passes_through_a_date_object():
    assert parse_date(date(2026, 1, 2)) == date(2026, 1, 2)


def test_parse_date_treats_empty_as_none():
    assert parse_date(None) is None
    assert parse_date("") is None
    assert parse_date("   ") is None


def test_parse_date_rejects_a_datetime():
    """A timestamp hides a caller bug, so it must not be silently truncated."""
    with pytest.raises(ValidationError, match="datetime"):
        parse_date(datetime(2026, 9, 17, 10, 0), "applied_on")


def test_parse_date_rejects_non_iso_text():
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        parse_date("17/09/2026", "applied_on")


def test_parse_date_rejects_impossible_calendar_dates():
    with pytest.raises(ValidationError, match="real calendar date"):
        parse_date("2026-02-30", "applied_on")


def test_parse_date_rejects_wrong_type():
    with pytest.raises(ValidationError, match="must be a date string"):
        parse_date(20260917, "applied_on")


# --------------------------------------------------------------------------
# Money
# --------------------------------------------------------------------------


def test_parse_money_reads_plain_digits_as_major_units():
    assert parse_money("120000") == 12_000_000


def test_parse_money_strips_separators_and_symbols():
    assert parse_money("120,000") == 12_000_000
    assert parse_money("$120,000") == 12_000_000
    assert parse_money("USD 120,000") == 12_000_000
    assert parse_money("120_000") == 12_000_000


def test_parse_money_keeps_cents():
    assert parse_money("120000.50") == 12_000_050


def test_parse_money_rounds_half_up_on_thousandths():
    assert parse_money("120000.005") == 12_000_001
    assert parse_money("120000.004") == 12_000_000


def test_parse_money_treats_ints_as_major_units_too():
    """Consistency with the text form: 120000 means 120,000 major units."""
    assert parse_money(120000) == 12_000_000


def test_parse_money_accepts_floats_via_repr():
    assert parse_money(120000.5) == 12_000_050


def test_parse_money_rejects_negative_values():
    with pytest.raises(ValidationError, match="cannot be negative"):
        parse_money("-5")


def test_parse_money_rejects_booleans():
    with pytest.raises(ValidationError, match="boolean"):
        parse_money(True)


def test_parse_money_rejects_garbage():
    with pytest.raises(ValidationError, match="not a valid amount"):
        parse_money("lots")


def test_parse_money_treats_empty_as_none():
    assert parse_money(None) is None
    assert parse_money("") is None


def test_format_money_renders_symbols_and_thousands():
    assert format_money(12_000_000, "USD") == "$120,000"
    assert format_money(12_000_050, "USD") == "$120,000.50"
    assert format_money(9_500_000, "EUR") == "\u20ac95,000"


def test_format_money_compact_mode():
    assert format_money(12_000_000, "USD", compact=True) == "$120k"
    assert format_money(250_000_000, "USD", compact=True) == "$2.5M"


def test_format_money_renders_none_as_em_dash():
    assert format_money(None) == "\u2014"


def test_format_range_collapses_equal_bounds():
    assert format_range(100_000, 100_000, "USD") == format_money(100_000, "USD")


def test_format_range_renders_both_bounds():
    assert format_range(100_000, 200_000, "USD") == "$1,000\u2013$2,000"


def test_format_range_handles_open_ended_bounds():
    assert format_range(None, 200_000, "USD").startswith("up to ")
    assert format_range(100_000, None, "USD").startswith("from ")


def test_format_range_handles_both_none():
    assert format_range(None, None, "USD") == "\u2014"


# --------------------------------------------------------------------------
# Text helpers
# --------------------------------------------------------------------------


def test_slugify_lowercases_and_hyphenates():
    assert slugify("Acme Corp") == "acme-corp"
    assert slugify("Senior  Backend   Engineer") == "senior-backend-engineer"


def test_slugify_ascii_folds_accents():
    assert slugify("Z\u00fcrich") == "zurich"
    assert slugify("Caf\u00e9 Ltd.") == "cafe-ltd"


def test_slugify_strips_edge_hyphens():
    assert slugify("  --Hello--  ") == "hello"


def test_make_id_is_stable_and_deterministic():
    assert make_id("Acme", "Backend Engineer") == "acme-backend-engineer"
    assert make_id("Acme", "Backend Engineer") == make_id("Acme", "Backend Engineer")


def test_make_id_suffixes_only_on_collision():
    taken = ["acme-engineer"]
    assert make_id("Acme", "Engineer", taken) == "acme-engineer-2"
    assert make_id("Acme", "Engineer", taken + ["acme-engineer-2"]) == "acme-engineer-3"


def test_clean_text_collapses_whitespace():
    assert clean_text("  a   b  ", "x") == "a b"


def test_clean_text_enforces_required():
    with pytest.raises(ValidationError, match="required"):
        clean_text("   ", "company", required=True)


def test_clean_text_enforces_max_length():
    with pytest.raises(ValidationError, match="too long"):
        clean_text("x" * 11, "company", max_len=10)


def test_clean_text_rejects_non_text():
    with pytest.raises(ValidationError, match="must be text"):
        clean_text(42, "company")


def test_one_of_normalises_case_spaces_and_hyphens():
    assert one_of("Remote", ("remote", "hybrid")) == "remote"
    assert one_of("job board", ("job_board",)) == "job_board"
    assert one_of("job-board", ("job_board",)) == "job_board"


def test_one_of_uses_default_for_blank():
    assert one_of("", ("remote",), "work_mode", default="remote") == "remote"


def test_one_of_rejects_unknown_values():
    with pytest.raises(ValidationError, match="must be one of"):
        one_of("teleport", ("remote",), "work_mode")


def test_parse_priority_defaults_to_three():
    assert parse_priority(None) == 3
    assert parse_priority("") == 3


def test_parse_priority_bounds():
    assert parse_priority("5") == 5
    with pytest.raises(ValidationError, match="between 1 and 5"):
        parse_priority(6)
    with pytest.raises(ValidationError, match="must be an integer"):
        parse_priority("high")


# --------------------------------------------------------------------------
# Vocabulary sanity
# --------------------------------------------------------------------------


def test_stage_vocabulary_shape():
    assert STAGES[0] == "applied"
    assert STAGES[-1] == "accepted"
    assert STATUSES[0] == "wishlist"
    assert set(ACTIVE).isdisjoint(set(CLOSED))
    assert {"rejected", "withdrawn"} <= set(STATUSES)


# --------------------------------------------------------------------------
# Application construction & validation
# --------------------------------------------------------------------------


def test_application_minimum_fields():
    app = make_app()
    assert app.id == "acme-swe"
    assert app.status == "wishlist"
    assert app.priority == 3
    assert app.currency == "USD"
    assert app.tags == []
    assert app.events == []


def test_application_requires_company_and_role():
    with pytest.raises(ValidationError, match="company"):
        Application(id="x", company="", role="Dev")
    with pytest.raises(ValidationError, match="role"):
        Application(id="x", company="Acme", role="")


def test_application_rejects_bad_id_shape():
    with pytest.raises(ValidationError, match="lowercase letters, digits and hyphens"):
        Application(id="Acme_SWE", company="Acme", role="Dev")


def test_application_normalises_currency_case():
    assert make_app(currency="eur").currency == "EUR"


def test_application_rejects_malformed_currency():
    with pytest.raises(ValidationError, match="3-letter code"):
        make_app(currency="DOLLARS")


def test_application_rejects_inverted_salary_range():
    with pytest.raises(ValidationError, match="salary_min cannot exceed salary_max"):
        make_app(status="wishlist", salary_min="200000", salary_max="100000")


def test_application_rejects_unknown_status():
    with pytest.raises(ValidationError, match="status must be one of"):
        make_app(status="ghosted")


def test_application_rejects_advanced_status_without_an_application_date():
    """Advancing past `applied` without ever applying is a data bug."""
    with pytest.raises(ValidationError, match="no applied date"):
        Application(id="x", company="Acme", role="Dev", status="interview")


def test_application_accepts_advanced_status_when_apply_is_logged():
    app = applied_app("acme-dev")
    app.status = "interview"
    app.validate()
    assert app.status == "interview"


def test_application_derives_closed_on_from_the_event_log():
    app = applied_app("acme-dev", "2026-09-01")
    app.events.append(
        Event(on="2026-09-10", kind="closed", from_status="applied", to_status="rejected")
    )
    app.status = "rejected"
    app.validate()
    assert app.closed_on == "2026-09-10"


def test_application_rejects_apply_before_create():
    with pytest.raises(ValidationError, match="before created_on"):
        Application(
            id="x",
            company="Acme",
            role="Dev",
            status="applied",
            created_on="2026-09-10",
            applied_on="2026-09-01",
        )


def test_application_tags_are_slugified_deduplicated_and_sorted():
    app = make_app(tags=["Python", "python", "Machine Learning"])
    assert app.tags == ["machine-learning", "python"]


def test_application_accepts_comma_separated_tag_string():
    assert make_app(tags="python, k8s python").tags == ["k8s", "python"]


def test_application_work_mode_defaults_to_unspecified():
    assert make_app().work_mode == "unspecified"


# --------------------------------------------------------------------------
# Derived state — the honesty rules
# --------------------------------------------------------------------------


def test_furthest_stage_is_none_for_a_wishlist_item():
    assert make_app().furthest_stage() is None
    assert make_app().reached_index() == -1


def test_furthest_stage_of_a_fresh_application_is_applied():
    assert applied_app("a").furthest_stage() == "applied"
    assert applied_app("a").reached_index() == 0


def test_rejection_after_an_onsite_still_counts_as_reaching_onsite():
    """The cohort-bias case: the funnel must not erase a rejection."""
    app = applied_app("cobalt", "2026-08-04")
    app.events.append(
        Event(on="2026-08-30", kind="moved", to_status="onsite", note="panel")
    )
    app.status = "rejected"
    app.closed_on = "2026-09-08"
    app.events.append(
        Event(on="2026-09-08", kind="closed", from_status="onsite", to_status="rejected")
    )
    app.validate()

    assert app.furthest_stage() == "onsite"
    assert app.reached_index() == STAGES.index("onsite")
    assert app.is_closed is True


def test_rejection_is_a_response_not_a_silence():
    app = applied_app("a")
    app.events.append(
        Event(on="2026-09-05", kind="closed", from_status="applied", to_status="rejected")
    )
    app.status = "rejected"
    app.validate()
    assert app.has_response() is True


def test_untouched_application_has_no_response():
    assert applied_app("a").has_response() is False


def test_stage_entry_event_counts_as_a_response():
    app = applied_app("a")
    app.events.append(Event(on="2026-09-06", kind="moved", to_status="screen"))
    app.status = "screen"
    app.validate()
    assert app.has_response() is True


def test_age_days_measures_from_the_application_date():
    assert applied_app("a", "2026-09-01").age_days(TODAY) == 16


def test_age_days_is_zero_when_never_applied():
    assert make_app().age_days(TODAY) == 0


def test_days_in_stage_uses_the_latest_entry_marker():
    app = applied_app("a", "2026-09-01")
    app.events.append(Event(on="2026-09-10", kind="moved", to_status="screen"))
    app.status = "screen"
    app.validate()
    assert app.days_in_stage(TODAY) == 7


def test_last_activity_considers_events_interviews_and_notes():
    app = applied_app("a", "2026-09-01")
    app.notes.append(Note(on="2026-09-12", text="called the recruiter"))
    app.validate()
    assert app.last_activity_on() == date(2026, 9, 12)
    assert app.days_since_activity(TODAY) == 5


def test_upcoming_interviews_excludes_done_and_past_ones():
    app = applied_app("a")
    app.interviews = [
        Interview(on="2026-09-20", kind="technical"),
        Interview(on="2026-09-15", kind="screen", done=True),
        Interview(on="2026-09-10", kind="screen"),
    ]
    upcoming = app.upcoming_interviews(TODAY)
    assert [i.on for i in upcoming] == ["2026-09-20"]


def test_response_helpers_read_the_event_log():
    app = applied_app("a")
    app.events.append(Event(on="2026-09-08", kind="followup", note="pinged"))
    assert app.followup_count() == 1
    assert app.last_followup_on() == date(2026, 9, 8)


def test_was_applied_true_from_a_date_or_from_an_event():
    assert applied_app("a").was_applied is True
    assert make_app().was_applied is False


# --------------------------------------------------------------------------
# Round-tripping
# --------------------------------------------------------------------------


def test_application_round_trips_through_dict_without_reconverting_money():
    original = applied_app("a", "2026-09-01", salary_min="100000", salary_max="140000")
    reloaded = Application.from_dict(original.to_dict())

    assert reloaded.salary_min == original.salary_min == 10_000_000
    assert reloaded.salary_max == original.salary_max == 14_000_000
    assert reloaded.to_dict() == original.to_dict()


def test_from_dict_rejects_a_non_object():
    with pytest.raises(ValidationError, match="must be an object"):
        Application.from_dict(["nope"])


def test_event_requires_a_valid_kind():
    with pytest.raises(ValidationError, match="event.kind"):
        Event(on="2026-09-01", kind="teleported")


def test_note_requires_text():
    with pytest.raises(ValidationError, match="note.text"):
        Note(on="2026-09-01", text="   ")
