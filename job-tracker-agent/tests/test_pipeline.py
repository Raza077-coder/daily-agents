"""Tests for :mod:`jobtracker.pipeline` — the verbs a job hunt actually uses."""

from __future__ import annotations

import pytest

from jobtracker.models import CLOSED, ValidationError
from jobtracker.store import Vault

from conftest import TODAY, applied_app, make_app

# --------------------------------------------------------------------------
# add
# --------------------------------------------------------------------------


def test_add_creates_a_wishlist_item_by_default(pipeline):
    app = pipeline.add("Acme", "Backend Engineer", today=TODAY)
    assert app.status == "wishlist"
    assert app.id == "acme-backend-engineer"
    assert app.created_on == TODAY.isoformat()
    assert app.applied_on is None


def test_add_writes_a_created_event(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    assert [e.kind for e in app.events] == ["created"]


def test_add_with_applied_status_records_an_applied_event(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    assert app.applied_on == TODAY.isoformat()
    assert "applied" in [e.kind for e in app.events]


def test_add_with_backdated_apply_uses_that_date(pipeline):
    app = pipeline.add(
        "Acme", "Dev", status="applied", applied_on="2026-09-01", today=TODAY
    )
    assert app.applied_on == "2026-09-01"
    assert app.created_on == "2026-09-01"
    assert app.age_days(TODAY) == 16


def test_add_records_an_imported_move_when_seeded_mid_pipeline(pipeline):
    """Importing history should be explicit, not silently implied."""
    app = pipeline.add(
        "Acme", "Dev", status="onsite", created_on="2026-08-01",
        applied_on="2026-08-02", today=TODAY,
    )
    kinds = [e.kind for e in app.events]
    assert "applied" in kinds
    assert "moved" in kinds


def test_add_rejects_an_unknown_status(pipeline):
    with pytest.raises(ValidationError, match="status must be one of"):
        pipeline.add("Acme", "Dev", status="ghosted", today=TODAY)


def test_add_parses_money_from_text(pipeline):
    app = pipeline.add(
        "Acme", "Dev", today=TODAY, salary_min="$100,000", salary_max="$140,000"
    )
    assert app.salary_min == 10_000_000
    assert app.salary_max == 14_000_000


def test_add_rejects_an_inverted_salary_range(pipeline):
    with pytest.raises(ValidationError, match="salary_min cannot exceed"):
        pipeline.add("Acme", "Dev", today=TODAY, salary_min="200000", salary_max="100000")


def test_add_many_imports_a_batch(pipeline):
    created = pipeline.add_many(
        [
            {"company": "Acme", "role": "Dev", "status": "applied", "applied_on": "2026-09-01"},
            {"company": "Globex", "role": "SRE", "status": "wishlist"},
        ],
        today=TODAY,
    )
    assert len(created) == 2
    assert len(pipeline.vault) == 2
    assert created[0].created_on == "2026-09-01"  # backdated with the submission


def test_add_many_rejects_rows_missing_company_or_role(pipeline):
    with pytest.raises(ValidationError, match="company"):
        pipeline.add_many([{"company": "Acme"}], today=TODAY)


# --------------------------------------------------------------------------
# move — the rung rules
# --------------------------------------------------------------------------


def test_move_advances_one_rung(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    moved = pipeline.move(app.id, "screen", today=TODAY)
    assert moved.status == "screen"
    assert any(e.to_status == "screen" for e in moved.events)


def test_move_refuses_to_skip_rungs_and_names_them(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    with pytest.raises(ValidationError, match="skips"):
        pipeline.move(app.id, "onsite", today=TODAY)


def test_move_force_allows_a_skip(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    moved = pipeline.move(app.id, "onsite", today=TODAY, force=True)
    assert moved.status == "onsite"


def test_move_refuses_backwards_progress(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.move(app.id, "screen", today=TODAY)
    pipeline.move(app.id, "interview", today=TODAY)
    with pytest.raises(ValidationError, match="backwards"):
        pipeline.move(app.id, "screen", today=TODAY)


def test_move_rejects_a_no_op(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    with pytest.raises(ValidationError, match="already"):
        pipeline.move(app.id, "wishlist", today=TODAY)


def test_a_wishlist_item_cannot_be_rejected_outright(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    with pytest.raises(ValidationError, match="never applied"):
        pipeline.move(app.id, "rejected", today=TODAY)


def test_a_closed_application_cannot_be_moved_forward(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.move(app.id, "rejected", today=TODAY)
    with pytest.raises(ValidationError, match="already closed"):
        pipeline.move(app.id, "screen", today=TODAY)


def test_rejecting_an_application_sets_closed_on(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    rejected = pipeline.move(app.id, "rejected", today=TODAY)
    assert rejected.closed_on == TODAY.isoformat()
    assert rejected.is_closed is True


def test_reopening_an_active_application_clears_closed_on(pipeline):
    """A rejection that turns into a callback."""
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.move(app.id, "rejected", today=TODAY)
    reopened = pipeline.reopen(app.id, today=TODAY)
    assert reopened.status == "applied"
    assert reopened.closed_on is None


def test_reopen_refuses_an_open_application(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    with pytest.raises(ValidationError, match="not closed"):
        pipeline.reopen(app.id, today=TODAY)


def test_move_records_the_note_on_the_event(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.move(app.id, "screen", today=TODAY, note="recruiter call booked")
    assert any(e.note == "recruiter call booked" for e in app.events)


# --------------------------------------------------------------------------
# log_apply / log_stage — honest backfilling
# --------------------------------------------------------------------------


def test_log_apply_backdates_the_submission(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    applied = pipeline.log_apply(app.id, on="2026-09-01", today=TODAY)
    assert applied.status == "applied"
    assert applied.applied_on == "2026-09-01"
    assert applied.created_on == "2026-09-01"
    assert applied.age_days(TODAY) == 16


def test_log_apply_refuses_a_future_date(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    with pytest.raises(ValidationError, match="future"):
        pipeline.log_apply(app.id, on="2026-10-01", today=TODAY)


def test_log_apply_refuses_a_closed_application(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.move(app.id, "withdrawn", today=TODAY)
    with pytest.raises(ValidationError, match="closed"):
        pipeline.log_apply(app.id, today=TODAY)


def test_log_stage_backfills_without_moving_the_status(pipeline):
    """The honest way to record history: 'this already happened'."""
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    updated = pipeline.log_stage(app.id, "onsite", on="2026-09-10", today=TODAY)
    assert updated.status == "applied"
    assert updated.furthest_stage() == "onsite"
    assert updated.reached_index() > 0


def test_log_stage_refuses_a_future_date(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    with pytest.raises(ValidationError, match="future"):
        pipeline.log_stage(app.id, "screen", on="2026-10-01", today=TODAY)
    assert "screen" not in [e.to_status for e in app.events]


def test_log_stage_refuses_the_applied_rung(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    with pytest.raises(ValidationError, match="log-apply"):
        pipeline.log_stage(app.id, "applied", today=TODAY)


# --------------------------------------------------------------------------
# interviews
# --------------------------------------------------------------------------


def test_schedule_interview_stores_it_pending(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    interview = pipeline.schedule_interview(app.id, "2026-09-20", kind="technical")
    assert interview.done is False
    assert app.upcoming_interviews(TODAY)[0].kind == "technical"


def test_scheduling_the_same_slot_twice_replaces_rather_than_duplicates(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.schedule_interview(app.id, "2026-09-20", kind="technical")
    pipeline.schedule_interview(app.id, "2026-09-20", kind="technical", note="moved room")
    assert len(app.interviews) == 1
    assert app.interviews[0].note == "moved room"


def test_completing_the_first_interview_advances_to_the_interview_rung(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.schedule_interview(app.id, "2026-09-20", kind="technical")
    updated = pipeline.complete_interview(app.id, on="2026-09-20", today=TODAY)
    assert updated.status == "interview"


def test_completing_a_screen_never_drags_an_advanced_application_backwards(pipeline):
    app = pipeline.add(
        "Acme", "Dev", status="applied", applied_on="2026-08-01", created_on="2026-08-01",
        today=TODAY,
    )
    pipeline.log_stage(app.id, "onsite", on="2026-09-10", today=TODAY)
    pipeline.schedule_interview(app.id, "2026-09-16", kind="screen")
    updated = pipeline.complete_interview(app.id, on="2026-09-16", today=TODAY)
    assert updated.reached_index() >= 2


def test_completing_a_nonexistent_interview_is_refused(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    with pytest.raises(ValidationError, match="no pending interview"):
        pipeline.complete_interview(app.id, today=TODAY)


def test_completed_interview_appears_in_the_event_log(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.schedule_interview(app.id, "2026-09-20", kind="final")
    pipeline.complete_interview(app.id, on="2026-09-20", today=TODAY)
    assert any(e.kind == "interview" for e in app.events)


# --------------------------------------------------------------------------
# notes / follow-ups / next actions
# --------------------------------------------------------------------------


def test_note_is_stored_and_evented(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    pipeline.note(app.id, "referred by Sara", today=TODAY)
    assert app.notes[0].text == "referred by Sara"
    assert any(e.kind == "note" for e in app.events)


def test_followup_requires_a_submitted_application(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    with pytest.raises(ValidationError, match="not been applied to yet"):
        pipeline.followup(app.id, today=TODAY)


def test_followup_on_a_closed_application_is_refused(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.move(app.id, "withdrawn", today=TODAY)
    with pytest.raises(ValidationError, match="nothing to chase"):
        pipeline.followup(app.id, today=TODAY)


def test_followup_records_the_channel(pipeline):
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    pipeline.followup(app.id, today=TODAY, channel="email", note="pinged the HM")
    assert app.followup_count() == 1
    assert any("[email]" in e.note for e in app.events)
    assert any("pinged the HM" in e.note for e in app.events)


def test_set_next_action_stores_the_commitment(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    updated = pipeline.set_next_action(app.id, "Email the hiring manager", on="2026-09-20")
    assert updated.next_action == "Email the hiring manager"
    assert updated.next_action_on == "2026-09-20"


# --------------------------------------------------------------------------
# edit / drop
# --------------------------------------------------------------------------


def test_edit_updates_a_scalar_field(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    updated = pipeline.edit(app.id, today=TODAY, location="Karachi, PK", priority=5)
    assert updated.location == "Karachi, PK"
    assert updated.priority == 5


def test_edit_refuses_an_unknown_field(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    with pytest.raises(ValidationError, match="cannot edit"):
        pipeline.edit(app.id, today=TODAY, nickname="nope")


def test_edit_routes_status_changes_through_move(pipeline):
    """So the event log stays honest."""
    app = pipeline.add("Acme", "Dev", status="applied", today=TODAY)
    updated = pipeline.edit(app.id, today=TODAY, status="screen")
    assert updated.status == "screen"
    assert any(e.to_status == "screen" for e in updated.events)


def test_edit_with_nothing_to_change_is_refused(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    with pytest.raises(ValidationError, match="nothing to edit"):
        pipeline.edit(app.id, today=TODAY)


def test_edit_records_which_fields_changed(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    pipeline.edit(app.id, today=TODAY, contact="marta@acme.example")
    edits = [e for e in app.events if e.kind == "edited"]
    assert edits and "contact" in edits[-1].note


def test_drop_removes_the_application(pipeline):
    app = pipeline.add("Acme", "Dev", today=TODAY)
    removed = pipeline.drop(app.id)
    assert removed.id == app.id
    assert len(pipeline.vault) == 0


def test_dropping_a_missing_id_raises(pipeline):
    with pytest.raises(Exception, match="no application with id"):
        pipeline.drop("nope")
