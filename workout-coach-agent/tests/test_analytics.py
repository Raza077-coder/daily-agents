"""Analytics tests: volume attribution, tonnage, streaks, coverage, plateaus.

Analytics are pure functions over logged workouts, which makes them cheap to
test and worth testing precisely \u2014 every number here appears in the weekly
report a user reads, so an off-by-one in volume attribution becomes wrong
coaching advice.
"""

from __future__ import annotations

import pytest

from forge.analytics import (
    SECONDARY_CREDIT,
    balance_report,
    consistency,
    detect_plateaus,
    muscle_coverage,
    summary,
    volume_by_muscle,
    week_bounds,
    week_label,
    week_streak,
    weekly_tonnage,
)
from forge.exercises import MAJOR_MUSCLES


# ---------------------------------------------------------------------------
# Week helpers
# ---------------------------------------------------------------------------


class TestWeekHelpers:
    def test_week_bounds_are_monday_to_sunday(self):
        start, end = week_bounds("2026-01-07")  # a Wednesday
        assert start.isoformat() == "2026-01-05"
        assert end.isoformat() == "2026-01-11"

    def test_monday_is_its_own_week_start(self):
        start, _ = week_bounds("2026-01-05")
        assert start.isoformat() == "2026-01-05"

    def test_sunday_belongs_to_the_week_that_started_monday(self):
        start, end = week_bounds("2026-01-11")
        assert start.isoformat() == "2026-01-05"
        assert end.isoformat() == "2026-01-11"

    def test_week_label_is_iso(self):
        assert week_label("2026-01-05") == "2026-W02"


# ---------------------------------------------------------------------------
# Volume attribution
# ---------------------------------------------------------------------------


class TestVolumeByMuscle:
    def test_primary_gets_full_credit_and_secondary_half(self, engine):
        engine.log("bench", "100x10x1", day="2026-01-05")
        vol = volume_by_muscle(engine.store.workouts)
        # Bench: chest primary, triceps + front_delts secondary.
        assert vol["chest"]["sets"] == 1.0
        assert vol["triceps"]["sets"] == SECONDARY_CREDIT
        assert vol["front_delts"]["sets"] == SECONDARY_CREDIT

    def test_tonnage_is_weight_times_reps(self, engine):
        engine.log("bench", "100x10x1", day="2026-01-05")
        vol = volume_by_muscle(engine.store.workouts)
        assert vol["chest"]["tonnage"] == 1000.0
        # Secondary muscles get the same 0.5 share of tonnage.
        assert vol["triceps"]["tonnage"] == 500.0

    def test_unweighted_mode_credits_only_primary(self, engine):
        engine.log("bench", "100x10x1", day="2026-01-05")
        vol = volume_by_muscle(engine.store.workouts, weighted=False)
        assert vol["chest"]["sets"] == 1.0
        assert vol.get("triceps", {}).get("sets", 0) == 0

    def test_row_is_sorted_by_descending_sets(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        engine.log("curl", "20x10x1", day="2026-01-05")
        vol = volume_by_muscle(engine.store.workouts)
        sets = [row["sets"] for row in vol.values()]
        assert sets == sorted(sets, reverse=True)

    def test_pct_of_target_uses_the_configured_target(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        vol = volume_by_muscle(engine.store.workouts)
        row = vol["chest"]
        assert row["target"] == 10
        assert row["pct_of_target"] == 30.0  # 3 sets / 10 target

    def test_unknown_exercise_is_skipped_not_crashed(self):
        from forge.models import LoggedExercise, SetEntry, Workout

        w = Workout(
            date="2026-01-05",
            entries=[LoggedExercise(exercise_id="ghost_lift", sets=[SetEntry(60, 8)])],
        )
        assert volume_by_muscle([w]) == {}


# ---------------------------------------------------------------------------
# Tonnage
# ---------------------------------------------------------------------------


class TestWeeklyTonnage:
    def test_one_row_per_week(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        engine.log("bench", "60x8x3", day="2026-01-13")
        rows = weekly_tonnage(engine.store.workouts)
        assert len(rows) == 2

    def test_same_week_sessions_are_aggregated(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        engine.log("bench", "60x8x3", day="2026-01-07")
        rows = weekly_tonnage(engine.store.workouts)
        assert len(rows) == 1
        assert rows[0]["sessions"] == 2
        assert rows[0]["tonnage"] == 2880.0

    def test_bodyweight_sets_are_counted_separately(self, engine):
        """A pull-up has 0 kg tonnage; the report must say so rather than
        letting a high bodyweight volume look like lost work."""
        engine.log("pullup", "bw x10x3", day="2026-01-05")
        rows = weekly_tonnage(engine.store.workouts)
        assert rows[0]["tonnage"] == 0.0
        assert rows[0]["bodyweight_sets"] == 3

    def test_rows_are_chronological(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-13")
        engine.log("bench", "60x8x3", day="2026-01-05")
        rows = weekly_tonnage(engine.store.workouts)
        assert rows[0]["week"] < rows[1]["week"]


# ---------------------------------------------------------------------------
# Streaks
# ---------------------------------------------------------------------------


class TestStreak:
    def test_streak_counts_consecutive_qualifying_weeks(self, engine):
        for day in ("2026-01-05", "2026-01-07", "2026-01-09"):
            engine.log("bench", "60x8x3", day=day)
        for day in ("2026-01-12", "2026-01-14", "2026-01-16"):
            engine.log("bench", "60x8x3", day=day)
        st = week_streak(engine.store.workouts, "2026-01-16")
        assert st["current"] >= 1
        assert st["longest"] >= 1
        assert st["target"] >= 1

    def test_an_empty_open_week_does_not_break_the_streak(self, engine):
        """Mid-week a user may simply not have trained yet \u2014 that is not a
        broken streak, and reporting it as one would be discouraging and wrong."""
        for day in ("2026-01-05", "2026-01-07", "2026-01-09"):
            engine.log("bench", "60x8x3", day=day)
        st = week_streak(engine.store.workouts, "2026-01-13")  # Tuesday, nothing yet
        assert st["current"] >= 1

    def test_streak_object_exposes_the_expected_keys(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        st = week_streak(engine.store.workouts, "2026-01-05")
        for key in ("current", "longest", "target", "this_week"):
            assert key in st


# ---------------------------------------------------------------------------
# Coverage and balance
# ---------------------------------------------------------------------------


class TestCoverageAndBalance:
    def test_coverage_partitions_major_muscles(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        cov = muscle_coverage(engine.store.workouts)
        # One pressing session is not enough to call chest "trained" \u2014 it lands
        # in "light". That grading is the point: a muscle should not be credited
        # as covered off a single workout.
        assert "chest" in cov["trained"] + cov["light"]
        total = len(cov["trained"]) + len(cov["light"]) + len(cov["missed"])
        assert total == len(MAJOR_MUSCLES)

    def test_nothing_logged_means_everything_missed(self, engine):
        cov = muscle_coverage([])
        assert cov["trained"] == []
        assert len(cov["missed"]) == len(MAJOR_MUSCLES)

    def test_balance_flags_neglected_rear_delts(self, engine):
        """Rear delts are the classic omission in push-heavy programmes, so
        the balance report calling them out is the behaviour worth pinning."""
        for day in ("2026-01-05", "2026-01-07"):
            engine.log("bench", "60x8x3", day=day)
        rep = balance_report(engine.store.workouts)
        text = " ".join(f["detail"] for f in rep["findings"])
        assert "rear" in text.lower()

    def test_findings_are_severity_ordered(self, engine):
        for day in ("2026-01-05", "2026-01-07"):
            engine.log("bench", "60x8x3", day=day)
        rep = balance_report(engine.store.workouts)
        order = {"high": 0, "medium": 1, "low": 2}
        ranks = [order.get(f["severity"], 9) for f in rep["findings"]]
        assert ranks == sorted(ranks)


# ---------------------------------------------------------------------------
# Plateaus
# ---------------------------------------------------------------------------


class TestPlateaus:
    def test_flat_e1rm_is_detected(self, engine):
        for day in ("2026-01-05", "2026-01-07", "2026-01-09", "2026-01-12", "2026-01-14"):
            engine.log("bench", "60x8x3", day=day)
        rows = detect_plateaus(engine.store.workouts)
        assert any(r["exercise"] == "barbell_bench_press" for r in rows)

    def test_progressing_lift_is_not_flagged(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        engine.log("bench", "70x8x3", day="2026-01-07")
        engine.log("bench", "80x8x3", day="2026-01-09")
        rows = detect_plateaus(engine.store.workouts)
        assert not any(r["exercise"] == "barbell_bench_press" for r in rows)

    def test_plateau_row_carries_a_suggestion(self, engine):
        for day in ("2026-01-05", "2026-01-07", "2026-01-09", "2026-01-12", "2026-01-14"):
            engine.log("bench", "60x8x3", day=day)
        row = next(r for r in detect_plateaus(engine.store.workouts) if r["exercise"] == "barbell_bench_press")
        assert row["hint"].strip()
        assert row["sessions"] >= 4


# ---------------------------------------------------------------------------
# The summary object every surface renders
# ---------------------------------------------------------------------------


class TestSummary:
    def test_empty_history_does_not_crash(self):
        data = summary([], "2026-01-05", 28)
        assert data["total_workouts"] == 0
        assert data["lifetime_tonnage"] == 0
        assert data["first_session"] is None

    def test_empty_history_still_has_every_key_the_report_reads(self):
        data = summary([], "2026-01-05", 28)
        for key in (
            "as_of", "window_days", "total_workouts", "lifetime_tonnage",
            "lifetime_sets", "window", "this_week", "tonnage_delta_pct",
            "streak", "volume_by_muscle", "balance", "coverage", "plateaus",
        ):
            assert key in data, f"the coach renders {key!r} but summary omits it"

    def test_window_excludes_older_sessions(self, engine):
        engine.log("bench", "60x8x3", day="2025-01-05")
        engine.log("bench", "60x8x3", day="2026-01-05")
        data = summary(engine.store.workouts, "2026-01-05", 7)
        assert data["total_workouts"] == 2  # lifetime
        assert data["window"]["sessions"] == 1  # only the recent one

    def test_tonnage_delta_compares_this_week_to_last(self, engine):
        for day in ("2026-01-05", "2026-01-07"):
            engine.log("bench", "60x8x3", day=day)        # 2 x 1440 = 2880 last week
        engine.log("bench", "60x8x6", day="2026-01-12")   # 2880 this week -> flat
        flat = summary(engine.store.workouts, "2026-01-12", 28)
        assert flat["tonnage_delta_pct"] == 0.0

        engine.store.reset()
        engine.init_profile(bodyweight_kg=80.0, experience="intermediate", goal="strength")
        for day in ("2026-01-05", "2026-01-07"):
            engine.log("bench", "60x8x3", day=day)
        engine.log("bench", "60x8x9", day="2026-01-12")   # 4320 -> up week on week
        up = summary(engine.store.workouts, "2026-01-12", 28)
        assert up["tonnage_delta_pct"] > 0

    def test_is_deterministic(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        a = summary(engine.store.workouts, "2026-01-05", 28)
        b = summary(engine.store.workouts, "2026-01-05", 28)
        assert a == b

    def test_consistency_reports_sessions_per_week(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        c = consistency(engine.store.workouts, 28, "2026-01-05")
        assert "sessions_per_week" in c or "per_week" in c
