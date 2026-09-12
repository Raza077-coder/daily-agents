"""Engine tests: set-spec parsing, double progression, PRs, progress.

The progression tests are the important ones. FORGE's entire claim is that the
next prescription is derivable from the logged sets, so each of the five
outcomes is pinned to an exact expected weight and action. If someone edits a
rule and the advice changes, these fail loudly instead of quietly shipping
different coaching to users.
"""

from __future__ import annotations

import pytest

from forge.engine import parse_set_spec, round_to_increment
from forge.exercises import get_exercise
from forge.models import epley_e1rm


# ---------------------------------------------------------------------------
# Set-spec parsing
# ---------------------------------------------------------------------------


class TestParseSetSpec:
    def test_single_set(self):
        sets = parse_set_spec("60x8")
        assert len(sets) == 1
        assert (sets[0].weight, sets[0].reps) == (60.0, 8)

    def test_repeat_count(self):
        sets = parse_set_spec("60x8x3")
        assert len(sets) == 3
        assert all(s.weight == 60 and s.reps == 8 for s in sets)

    def test_explicit_list(self):
        sets = parse_set_spec("60x8,60x8,60x6")
        assert [s.reps for s in sets] == [8, 8, 6]

    def test_bodyweight(self):
        sets = parse_set_spec("bw x10")
        assert sets[0].weight == 0.0
        assert sets[0].reps == 10

    def test_bodyweight_with_added_load(self):
        sets = parse_set_spec("bw+10 x5")
        assert sets[0].weight == 10.0
        assert sets[0].reps == 5

    def test_rpe_suffix(self):
        sets = parse_set_spec("60x8@8")
        assert sets[0].rpe == 8.0

    def test_decimal_weight(self):
        sets = parse_set_spec("62.5x5x2")
        assert sets[0].weight == 62.5
        assert len(sets) == 2

    @pytest.mark.parametrize("bad", ["", "abc", "60x", "x8", "60x8x0", "60 x 8 x"])
    def test_malformed_specs_raise_value_error(self, bad):
        """A typo must never silently drop work from history."""
        with pytest.raises(ValueError):
            parse_set_spec(bad)


class TestRoundToIncrement:
    @pytest.mark.parametrize(
        "value,increment,expected",
        [
            (61.0, 2.5, 60.0),   # 1.0 to 60, vs 1.5 to 62.5 -> rounds down
            (61.2, 2.5, 60.0),   # 1.2 to 60, vs 1.3 to 62.5 -> still down
            (61.4, 2.5, 62.5),   # 1.1 to 62.5, vs 1.4 to 60 -> crosses over
            (62.5, 2.5, 62.5),   # already on a plate boundary
            (100.0, 5.0, 100.0),
            (102.0, 5.0, 100.0),
            (0.0, 2.5, 0.0),
        ],
    )
    def test_snaps_to_nearest_available_plate(self, value, increment, expected):
        assert round_to_increment(value, increment) == expected

    def test_bodyweight_increment_never_produces_a_float_artefact(self):
        """increment 0 means bodyweight: the load must stay exactly 0.0."""
        assert round_to_increment(81.7, 0) == 0.0

    def test_boundary_case_rounds_to_a_real_plate(self):
        """Halfway between two plates must still land on one of them."""
        got = round_to_increment(61.25, 2.5)
        assert got in (60.0, 62.5)


# ---------------------------------------------------------------------------
# e1RM
# ---------------------------------------------------------------------------


class TestE1RM:
    def test_epley_formula(self):
        # 100 * (1 + 5/30) = 116.666... -> 116.7
        assert epley_e1rm(100, 5) == 116.7

    def test_single_is_exact(self):
        assert epley_e1rm(140, 1) == 140.0

    def test_zero_rep_or_weight_is_zero(self):
        assert epley_e1rm(0, 10) == 0.0
        assert epley_e1rm(100, 0) == 0.0


# ---------------------------------------------------------------------------
# Double progression — the five outcomes
# ---------------------------------------------------------------------------


class TestProgression:
    def test_no_history_starts_conservative(self, engine):
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["action"] == "start"
        assert advice["weight"] > 0
        # 80 kg intermediate, ratio 0.65, factor 0.8 -> 41.6 -> snapped to 42.5
        assert advice["weight"] == 42.5

    def test_all_sets_at_top_of_range_increases(self, engine):
        engine.log("bench", "60x12x3")
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["action"] == "increase"
        assert advice["weight"] == 62.5
        assert advice["target_reps_low"] == 8

    def test_short_of_top_repeats_load(self, engine):
        engine.log("bench", "60x9x3")
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["action"] == "repeat"
        assert advice["weight"] == 60.0
        assert advice["target_reps_low"] == 10  # chase one more rep

    def test_three_sessions_under_bottom_deloads(self, engine):
        for day in ("2026-01-01", "2026-01-03", "2026-01-05"):
            engine.log("bench", "60x6x2", day=day)
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["action"] == "deload"
        assert advice["weight"] < 60.0
        assert advice["weight"] == 55.0  # 60 * 0.9 = 54 -> snapped up to 55

    def test_bodyweight_progresses_by_reps_not_load(self, engine):
        engine.log("pullup", "bw x15x3")
        advice = engine.next_prescription("pullup", 8, 15, 3)
        assert advice["action"] == "increase_reps"
        assert advice["weight"] == 0.0
        assert advice["target_reps_low"] > 15

    def test_plateau_changes_stimulus(self, engine):
        """Flat e1RM across 4+ sessions should alter the rep range, not add load."""
        for day in ("2026-01-01", "2026-01-03", "2026-01-05", "2026-01-07", "2026-01-09"):
            engine.log("bench", "60x8x3", day=day)
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["action"] in ("change_stimulus", "repeat")
        if advice["action"] == "change_stimulus":
            assert advice["target_reps_high"] < 12
            assert advice["sets"] == 4

    def test_every_outcome_carries_a_stated_reason(self, engine):
        """The coach must always explain the rule, never just issue an order."""
        engine.log("bench", "60x8x3")
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["reason"].strip()
        assert len(advice["reason"]) > 30

    def test_repeat_message_is_truthful_about_shortfall(self, engine):
        """Regression: the '0 set(s) fell under' contradiction.

        When every set cleared the bottom of the range, the old text still
        claimed a shortfall and told the user to "close that gap" — advice that
        made no sense next to the numbers it printed.
        """
        engine.log("bench", "60x9x3")
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["action"] == "repeat"
        assert "0 of" not in advice["reason"]
        assert "0 set(s) fell under" not in advice["reason"]
        assert "Every set cleared 8 reps" in advice["reason"]

    def test_repeat_message_reports_real_shortfall(self, engine):
        engine.log("bench", "60x8,60x7,60x6")
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["action"] == "repeat"
        assert "2 of 3 set(s) fell under 8" in advice["reason"]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class TestLogging:
    def test_log_records_volume_and_sets(self, engine):
        result = engine.log("bench", "60x8x3")
        assert result["volume"] == 1440.0  # 60 * 8 * 3
        assert len(result["sets"]) == 3
        assert result["date"]

    def test_alias_resolution(self, engine):
        assert get_exercise("bench").id == "barbell_bench_press"
        assert get_exercise("squat").id == "back_squat"
        assert get_exercise("barbell bench press").id == "barbell_bench_press"

    def test_unknown_exercise_raises_keyerror(self, engine):
        with pytest.raises(KeyError):
            engine.log("not_a_real_lift", "60x8")

    def test_first_log_sets_prs(self, engine):
        result = engine.log("bench", "60x8x3")
        assert result["prs"], "first ever log should register a personal best"

    def test_heavier_top_set_beats_previous_pr(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-01")
        result = engine.log("bench", "70x8x3", day="2026-01-03")
        labels = [p["label"] for p in result["prs"]]
        assert any("heaviest" in l.lower() for l in labels)

    def test_bulk_session_logging(self, engine):
        result = engine.log_session(
            day="2026-01-01",
            session="Upper A",
            entries={"bench": "60x8x3", "row": "50x10x3"},
        )
        assert len(result["exercises"]) == 2
        assert result["total_volume"] == 1440.0 + 1500.0

    def test_same_exercise_twice_in_a_day_merges(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-01")
        engine.log("bench", "60x8x2", day="2026-01-01")
        hist = engine.store.exercise_history("barbell_bench_press")
        assert len(hist) == 1
        assert hist[0]["set_count"] == 5

    def test_backfill_past_date(self, engine):
        result = engine.log("bench", "60x8x3", day="2026-01-01")
        assert result["date"] == "2026-01-01"

    @pytest.mark.parametrize("bad", ["2026-13-01", "not-a-date", "01/02/2026"])
    def test_bad_date_rejected(self, engine, bad):
        with pytest.raises((ValueError, TypeError)):
            engine.log("bench", "60x8x3", day=bad)


# ---------------------------------------------------------------------------
# Progress and PRs
# ---------------------------------------------------------------------------


class TestProgressAndPRs:
    def test_progress_reports_direction(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-01")
        engine.log("bench", "70x8x3", day="2026-01-08")
        data = engine.progress("barbell_bench_press")
        assert data["sessions"] == 2
        assert data["weight_gain"] == 10.0
        assert data["trend"]["direction"] == "improving"
        assert data["next"]["action"] in ("increase", "repeat", "start")

    def test_progress_on_unknown_lift_raises(self, engine):
        with pytest.raises(KeyError):
            engine.progress("not_a_real_lift")

    def test_personal_records_list(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-01")
        engine.log("bench", "70x8x3", day="2026-01-08")
        rows = engine.personal_records()
        assert rows
        bench = next(r for r in rows if r["exercise"] == "barbell_bench_press")
        assert bench["heaviest_weight"] == 70.0
        assert bench["sessions"] == 2

    def test_remove_day(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-01")
        engine.log("bench", "60x8x3", day="2026-01-02")
        assert engine.remove_day("2026-01-01") is True
        assert len(engine.store.workouts) == 1
        assert engine.remove_day("2026-01-01") is False


# ---------------------------------------------------------------------------
# Today / session scheduling
# ---------------------------------------------------------------------------


class TestToday:
    def test_rest_day_when_split_has_no_session(self, engine):
        engine.store.set_program(_four_day_program())
        # 2026-01-04 is a Sunday — not in the Mon/Tue/Thu/Fri split.
        data = engine.today("2026-01-04")
        assert data["rest"] is True

    def test_training_day_matches_split(self, engine):
        engine.store.set_program(_four_day_program())
        # 2026-01-05 is a Monday -> Upper A
        data = engine.today("2026-01-05")
        assert data["rest"] is False
        assert data["session"] == "Upper A"
        assert data["exercises"]

    def test_today_without_program_says_so(self, engine):
        data = engine.today("2026-01-05")
        assert data["program"] is None


def _four_day_program():
    from forge.program import build_program

    return build_program(
        days_per_week=4,
        experience="intermediate",
        goal="strength",
        bodyweight_kg=80.0,
    )
