"""Tests for :mod:`jobtracker.followups` — the ranked, explained to-do list."""

from __future__ import annotations

from datetime import timedelta

import pytest

from jobtracker.followups import (
    RULE_LABELS,
    RULE_ORDER,
    SEVERITIES,
    actions,
    days_left_in_week,
    explain_action,
    plan,
)
from jobtracker.models import Event, Interview
from jobtracker.store import Vault

from conftest import TODAY, applied_app, make_app


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def days_ahead(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def rules_for(vault, today=TODAY, config=None):
    return [a.rule for a in actions(vault, today, config)]


# --------------------------------------------------------------------------
# Contract
# --------------------------------------------------------------------------


def test_every_rule_has_a_label():
    assert set(RULE_ORDER) == set(RULE_LABELS)


def test_plan_on_an_empty_vault_still_asks_for_applications(vault):
    """With a weekly target set, an empty plan would be the wrong answer."""
    data = plan(vault, TODAY)
    assert data["total"] == 1
    assert data["actions"][0]["rule"] == "weekly_target"
    assert "thing(s) worth doing" in data["headline"]


def test_plan_on_an_empty_vault_is_empty_when_the_target_is_off(vault):
    data = plan(vault, TODAY, config={"weekly_target": 0})
    assert data["total"] == 0
    assert "Nothing is burning" in data["headline"]


def test_every_action_carries_a_readable_explanation(vault):
    """The house rule: never produce an action without a reason."""
    vault.add(applied_app("quiet", days_ago(60)))
    for action in actions(vault, TODAY):
        payload = action.to_dict()
        assert payload["explain"]
        assert payload["action"]
        assert payload["severity"] in SEVERITIES


def test_actions_are_ranked_by_score_descending(vault):
    vault.add(applied_app("quiet", days_ago(90)))
    found = actions(vault, TODAY)
    scores = [a.score for a in found]
    assert scores == sorted(scores, reverse=True)


def test_plan_respects_the_limit(vault):
    for i in range(12):
        vault.add(applied_app(f"a{i}", days_ago(90)))
    data = plan(vault, TODAY, limit=4)
    assert len(data["actions"]) == 4
    assert data["total"] >= 4


def test_plan_reports_severity_buckets(vault):
    vault.add(applied_app("quiet", days_ago(90)))
    data = plan(vault, TODAY)
    assert set(data["by_severity"]) == set(SEVERITIES)
    assert sum(data["by_severity"].values()) == data["total"]


def test_plan_echoes_the_effective_config(vault):
    data = plan(vault, TODAY, config={"weekly_target": 9})
    assert data["config"]["weekly_target"] == 9


# --------------------------------------------------------------------------
# Silence rules
# --------------------------------------------------------------------------


def test_a_quiet_application_produces_a_follow_up_nudge(vault):
    vault.add(applied_app("quiet", days_ago(30)))
    assert "no_response_overdue" in rules_for(vault)


def test_a_quiet_application_mentions_the_numbers_in_the_reason(vault):
    vault.add(applied_app("quiet", days_ago(30)))
    action = next(a for a in actions(vault, TODAY) if a.rule == "no_response_overdue")
    assert "30 days since the last activity" in action.explain()
    assert f"threshold {action.details['threshold']} days" in action.explain()


def test_a_recently_active_application_produces_no_silence_rule(vault):
    # 10 days quiet is inside the default 14-day applied threshold.
    vault.add(applied_app("busy", days_ago(10)))
    rules = rules_for(vault)
    assert "no_response_overdue" not in rules
    assert "second_followup" not in rules
    assert "stalled" not in rules


def test_one_follow_up_already_sent_escalates_to_a_final_chase(vault):
    app = applied_app("chased", days_ago(40))
    app.events.append(Event(on=days_ago(20), kind="followup"))
    app.validate()
    vault.add(app)
    assert "second_followup" in rules_for(vault)


def test_max_followups_flips_the_advice_to_stop(vault):
    app = applied_app("exhausted", days_ago(90))
    app.events.append(Event(on=days_ago(70), kind="followup"))
    app.events.append(Event(on=days_ago(40), kind="followup"))
    app.validate()
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "stalled")
    assert "Stop chasing" in action.action


def test_an_open_offer_is_not_also_given_follow_up_advice(vault):
    """Contradictory advice on the same card would be worse than none."""
    app = applied_app("offer", days_ago(30))
    app.status = "offer"
    app.events.append(Event(on=days_ago(20), kind="moved", to_status="offer"))
    app.validate()
    vault.add(app)

    rules = rules_for(vault)
    assert "offer_expiring" in rules
    assert "no_response_overdue" not in rules


def test_offer_open_for_five_days_is_critical(vault):
    app = applied_app("offer", days_ago(20))
    app.status = "offer"
    app.events.append(Event(on=days_ago(6), kind="moved", to_status="offer"))
    app.validate()
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "offer_expiring")
    assert action.severity == "critical"


def test_a_fresh_offer_is_high_not_critical(vault):
    app = applied_app("offer", days_ago(10))
    app.status = "offer"
    app.events.append(Event(on=days_ago(1), kind="moved", to_status="offer"))
    app.validate()
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "offer_waiting")
    assert action.severity == "high"


# --------------------------------------------------------------------------
# Deadlines & interviews
# --------------------------------------------------------------------------


def test_a_wishlist_deadline_today_is_critical(vault):
    vault.add(make_app("urgent", deadline_on=TODAY.isoformat()))
    action = next(a for a in actions(vault, TODAY) if a.rule == "deadline_today")
    assert action.severity == "critical"


def test_a_deadline_within_three_days_is_flagged(vault):
    vault.add(make_app("soon", deadline_on=days_ahead(2)))
    assert "deadline_soon" in rules_for(vault)


def test_a_passed_deadline_is_not_nagged_about(vault):
    vault.add(make_app("missed", deadline_on=days_ago(2)))
    rules = rules_for(vault)
    assert "deadline_today" not in rules
    assert "deadline_soon" not in rules


def test_an_interview_today_is_critical(vault):
    app = applied_app("iv", days_ago(10))
    app.interviews.append(Interview(on=TODAY.isoformat(), kind="technical"))
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "interview_today")
    assert action.severity == "critical"


def test_an_interview_tomorrow_is_high(vault):
    app = applied_app("iv", days_ago(10))
    app.interviews.append(Interview(on=days_ahead(1), kind="final"))
    vault.add(app)
    assert "interview_soon" in rules_for(vault)


def test_an_interview_next_week_is_medium_prep(vault):
    app = applied_app("iv", days_ago(10))
    app.interviews.append(Interview(on=days_ahead(5), kind="panel"))
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "interview_prep")
    assert action.severity == "medium"
    assert "prepare the stories" in action.explain()


def test_a_completed_interview_gives_no_upcoming_nudge(vault):
    app = applied_app("iv", days_ago(10))
    app.interviews.append(Interview(on=days_ahead(2), kind="final", done=True))
    vault.add(app)
    rules = rules_for(vault)
    assert "interview_soon" not in rules
    assert "interview_prep" not in rules


# --------------------------------------------------------------------------
# Commitments
# --------------------------------------------------------------------------


def test_an_overdue_next_action_fires(vault):
    app = applied_app("committed", days_ago(20))
    app.next_action = "Send the take-home"
    app.next_action_on = days_ago(4)
    app.validate()
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "overdue_next_action")
    assert action.severity == "high"
    assert "4 day(s) ago" in action.explain()


def test_a_long_overdue_commitment_escalates_to_critical(vault):
    app = applied_app("slipped", days_ago(40))
    app.next_action = "Chase the recruiter"
    app.next_action_on = days_ago(9)
    app.validate()
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "overdue_next_action")
    assert action.severity == "critical"


def test_a_future_commitment_does_not_fire(vault):
    app = applied_app("planned", days_ago(10))
    app.next_action = "Email on Monday"
    app.next_action_on = days_ahead(3)
    app.validate()
    vault.add(app)
    assert "overdue_next_action" not in rules_for(vault)


# --------------------------------------------------------------------------
# Thank-yous & learning
# --------------------------------------------------------------------------


def test_an_unthanked_recent_interview_fires(vault):
    app = applied_app("iv", days_ago(20))
    app.interviews.append(Interview(on=days_ago(2), kind="technical", done=True))
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "close_the_loop")
    assert "2 day(s) ago" in action.summary
    assert app.company in action.action


def test_a_thank_you_already_sent_suppresses_the_rule(vault):
    app = applied_app("iv", days_ago(20))
    app.interviews.append(Interview(on=days_ago(2), kind="technical", done=True))
    app.events.append(Event(on=days_ago(1), kind="followup", note="thank-you sent"))
    app.validate()
    vault.add(app)
    assert "close_the_loop" not in rules_for(vault)


def test_a_stale_interview_no_longer_needs_a_thank_you(vault):
    app = applied_app("iv", days_ago(40))
    app.interviews.append(Interview(on=days_ago(8), kind="technical", done=True))
    vault.add(app)
    assert "close_the_loop" not in rules_for(vault)


def test_a_rejection_after_an_interview_triggers_a_post_mortem(vault):
    app = applied_app("tough", days_ago(40))
    app.events.append(Event(on=days_ago(20), kind="moved", to_status="interview"))
    app.status = "rejected"
    app.closed_on = days_ago(3)
    app.events.append(Event(on=days_ago(3), kind="closed", to_status="rejected"))
    app.validate()
    vault.add(app)

    action = next(a for a in actions(vault, TODAY) if a.rule == "rejection_review")
    assert "post-mortem" in action.action


def test_a_rejection_before_any_interview_does_not_trigger_a_post_mortem(vault):
    app = applied_app("early", days_ago(40))
    app.status = "rejected"
    app.closed_on = days_ago(3)
    app.events.append(Event(on=days_ago(3), kind="closed", to_status="rejected"))
    app.validate()
    vault.add(app)
    assert "rejection_review" not in rules_for(vault)


def test_an_already_reviewed_rejection_does_not_fire_again(vault):
    app = applied_app("tough", days_ago(40))
    app.events.append(Event(on=days_ago(20), kind="moved", to_status="interview"))
    app.status = "rejected"
    app.closed_on = days_ago(3)
    app.events.append(Event(on=days_ago(3), kind="closed", to_status="rejected"))
    app.validate()
    from jobtracker.models import Note

    app.notes.append(Note(on=days_ago(2), text="Post-mortem: system design went thin."))
    vault.add(app)
    assert "rejection_review" not in rules_for(vault)


# --------------------------------------------------------------------------
# Wishlist
# --------------------------------------------------------------------------


def test_a_wishlist_item_can_rot(vault):
    from jobtracker.models import Note

    app = make_app("rotting", created_on=days_ago(30))
    app.notes.append(Note(on=days_ago(30), text="looks interesting"))
    vault.add(app)
    assert "wishlist_nudge" in rules_for(vault)


def test_a_fresh_wishlist_item_is_left_alone(vault):
    vault.add(make_app("fresh"))
    assert "wishlist_nudge" not in rules_for(vault)


def test_the_wishlist_rule_can_be_disabled(vault):
    app = make_app("rotting")
    from jobtracker.models import Note

    app.notes.append(Note(on=days_ago(30), text="looks interesting"))
    vault.add(app)
    rules = rules_for(vault, config={"wishlist_idle_days": 0})
    assert "wishlist_nudge" not in rules


# --------------------------------------------------------------------------
# Pipeline-level rules
# --------------------------------------------------------------------------


def test_the_weekly_target_fires_when_the_week_is_short(vault):
    vault.add(applied_app("one", days_ago(1)))
    action = next(a for a in actions(vault, TODAY) if a.rule == "weekly_target")
    assert action.details["target"] == 5
    assert action.details["shortfall"] >= 1


def test_the_weekly_target_stays_quiet_when_met(vault):
    for i in range(6):
        vault.add(applied_app(f"a{i}", TODAY.isoformat()))
    assert "weekly_target" not in rules_for(vault)


def test_the_weekly_target_can_be_disabled(vault):
    rules = rules_for(vault, config={"weekly_target": 0})
    assert "weekly_target" not in rules


def test_days_left_in_week_counts_today(vault):
    assert days_left_in_week(TODAY) == 7 - TODAY.weekday()


def test_log_wins_fires_for_a_fresh_offer(vault):
    app = applied_app("won", days_ago(30))
    app.status = "offer"
    app.events.append(Event(on=days_ago(2), kind="moved", to_status="offer"))
    app.validate()
    vault.add(app)
    assert "log_wins" in rules_for(vault)


def test_log_wins_stays_quiet_for_an_old_offer(vault):
    app = applied_app("old", days_ago(90))
    app.status = "offer"
    app.events.append(Event(on=days_ago(30), kind="moved", to_status="offer"))
    app.validate()
    vault.add(app)
    assert "log_wins" not in rules_for(vault)


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------


def test_followup_days_config_overrides_the_per_status_threshold(vault):
    """followup_days must actually be read, not merely documented.

    With no explicit value the per-status table applies (14 days for
    `applied`), so a 10-day-old application is quiet by choice; lowering
    followup_days to 5 must make it fire.
    """
    vault.add(applied_app("a", days_ago(10)))
    assert "no_response_overdue" not in rules_for(vault)
    assert "no_response_overdue" in rules_for(vault, config={"followup_days": 5})


def test_the_plan_is_never_empty_when_a_target_is_set(vault):
    """A hunt with no applications should still get told to send some."""
    assert plan(vault, TODAY)["total"] >= 1


# --------------------------------------------------------------------------
# explain
# --------------------------------------------------------------------------


def test_explain_returns_the_matching_action(vault):
    vault.add(applied_app("quiet", days_ago(60)))
    result = explain_action(vault, TODAY, "no_response_overdue")
    assert result["found"] is True
    assert result["action"]["rule"] == "no_response_overdue"


def test_explain_can_scope_to_one_application(vault):
    vault.add(applied_app("a-quiet", days_ago(60)))
    vault.add(applied_app("b-quiet", days_ago(60)))
    result = explain_action(vault, TODAY, "no_response_overdue", app_id="b-quiet")
    assert result["action"]["app_id"] == "b-quiet"


def test_explain_reports_a_rule_that_did_not_fire(vault):
    result = explain_action(vault, TODAY, "stalled")
    assert result["found"] is False
    assert "did not fire" in result["reason"]


def test_explain_names_an_unknown_rule_clearly(vault):
    result = explain_action(vault, TODAY, "teleport")
    assert result["found"] is False
    assert "unknown rule" in result["reason"]
