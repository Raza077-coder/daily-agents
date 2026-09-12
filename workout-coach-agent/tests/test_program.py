"""Program builder tests.

The builder is prescriptive, so its output is asserted concretely: which split
comes out for each day count, that every session is filled with exercises the
athlete's equipment actually allows, and \u2014 most importantly \u2014 that an
upper-body day can never be handed a leg isolation movement.
"""

from __future__ import annotations

import pytest

from forge.exercises import EQUIPMENT_RANK, EXERCISES
from forge.program import (
    SPLITS,
    WEEKLY_SET_CAP,
    build_program,
    choose_split,
    project_overload,
)


# ---------------------------------------------------------------------------
# Split selection
# ---------------------------------------------------------------------------


class TestSplitSelection:
    @pytest.mark.parametrize(
        "days,expected",
        [
            (2, "full_body_2"),
            (3, "full_body_3"),
            (4, "upper_lower_4"),
            (5, "ppl_5"),
            (6, "ppl_6"),
        ],
    )
    def test_standard_split_per_day_count(self, days, expected):
        assert choose_split(days) == expected

    @pytest.mark.parametrize("days", [1, 0, -5])
    def test_fewer_than_two_days_clamps_to_two(self, days):
        assert choose_split(days) == "full_body_2"

    @pytest.mark.parametrize("days", [7, 10, 99])
    def test_more_than_six_days_clamps_to_six(self, days):
        assert choose_split(days) == "ppl_6"

    def test_every_split_has_sessions_matching_its_day_count(self):
        for key, split in SPLITS.items():
            assert len(split["sessions"]) == split["days"], key

    def test_unknown_split_raises(self):
        with pytest.raises(KeyError):
            build_program(days_per_week=4, split="not_a_split")


# ---------------------------------------------------------------------------
# Session content
# ---------------------------------------------------------------------------


class TestSessionContent:
    @pytest.mark.parametrize("days", [2, 3, 4, 5, 6])
    def test_every_session_is_populated(self, days):
        prog = build_program(
            days_per_week=days, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        assert len(prog.sessions) == days
        for sess in prog.sessions:
            assert sess.exercises, f"{sess.title} on a {days}-day split came out empty"

    @pytest.mark.parametrize("days", [2, 3, 4, 5, 6])
    def test_no_upper_day_gets_a_leg_isolation(self, days):
        """The regression this suite exists for.

        Isolation slots on upper/push/pull days used to fall back to *any*
        isolation movement, so Leg Extension and Leg Curl appeared on Pull Day.
        The picker now relaxes the movement pattern before the muscle, which
        makes this impossible.
        """
        forbidden = {"quads", "hamstrings", "calves", "glutes"}
        prog = build_program(
            days_per_week=days, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        for sess in prog.sessions:
            if not sess.title.startswith(("Upper", "Push", "Pull")):
                continue
            for pe in sess.exercises:
                ex = EXERCISES[pe.exercise_id]
                if ex.kind == "isolation":
                    assert ex.primary not in forbidden, (
                        f"{ex.name} ({ex.primary}) landed on {sess.title}"
                    )

    def test_push_day_has_no_pulling_movements(self):
        prog = build_program(
            days_per_week=5, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        push = next(s for s in prog.sessions if s.title == "Push")
        for pe in push.exercises:
            assert EXERCISES[pe.exercise_id].pattern not in ("horizontal_pull", "vertical_pull")

    def test_pull_day_has_no_pressing_movements(self):
        prog = build_program(
            days_per_week=5, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        pull = next(s for s in prog.sessions if s.title == "Pull")
        for pe in pull.exercises:
            assert EXERCISES[pe.exercise_id].pattern not in ("horizontal_push", "vertical_push")


# ---------------------------------------------------------------------------
# The strength-programme quality fix
# ---------------------------------------------------------------------------


class TestExercisePreference:
    """Regression: alphabetical tie-breaking picked bodyweight over barbell.

    With a full gym the candidate list used to be sorted by (compound, name),
    so Back Extension beat Romanian Deadlift for the hinge slot and Chin-Up beat
    Overhead Press for the vertical push. Equipment now ranks candidates.
    """

    def test_barbell_ranks_ahead_of_bodyweight(self):
        assert EQUIPMENT_RANK["barbell"] < EQUIPMENT_RANK["bodyweight"]
        assert EQUIPMENT_RANK["barbell"] < EQUIPMENT_RANK["dumbbell"]

    def test_strength_hinge_slot_gets_a_loadable_lift(self):
        prog = build_program(
            days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        hinges = [
            EXERCISES[pe.exercise_id]
            for s in prog.sessions
            for pe in s.exercises
            if EXERCISES[pe.exercise_id].pattern == "hinge"
        ]
        assert hinges
        # A barbell hinge must be present, not only bodyweight back extensions.
        assert any(h.equipment == "barbell" for h in hinges)

    def test_upper_day_has_a_loadable_vertical_push(self):
        prog = build_program(
            days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        upper = next(s for s in prog.sessions if s.title == "Upper A")
        patterns = {EXERCISES[pe.exercise_id].pattern for pe in upper.exercises}
        assert "vertical_push" in patterns
        vp = next(
            EXERCISES[pe.exercise_id]
            for pe in upper.exercises
            if EXERCISES[pe.exercise_id].pattern == "vertical_push"
        )
        assert vp.equipment != "bodyweight", (
            "an intermediate strength programme should not start with pike push-ups"
        )


# ---------------------------------------------------------------------------
# Equipment filtering
# ---------------------------------------------------------------------------


class TestEquipmentFiltering:
    def test_home_gym_only_gets_available_equipment(self):
        prog = build_program(
            days_per_week=3,
            experience="intermediate",
            goal="hypertrophy",
            bodyweight_kg=75,
            equipment=["dumbbell", "bodyweight"],
        )
        allowed = {"dumbbell", "bodyweight"}
        for sess in prog.sessions:
            for pe in sess.exercises:
                assert EXERCISES[pe.exercise_id].equipment in allowed

    def test_bodyweight_is_always_available(self):
        prog = build_program(
            days_per_week=3, experience="beginner", goal="general",
            bodyweight_kg=70, equipment=["barbell"],
        )
        allowed = {"barbell", "bodyweight"}
        for sess in prog.sessions:
            for pe in sess.exercises:
                assert EXERCISES[pe.exercise_id].equipment in allowed

    def test_no_equipment_list_means_full_gym(self, engine):
        assert engine.available_equipment() == []
        prog = build_program(days_per_week=4, experience="intermediate", goal="strength")
        assert all(s.exercises for s in prog.sessions)


# ---------------------------------------------------------------------------
# Dosing and volume caps
# ---------------------------------------------------------------------------


class TestDosing:
    def test_goal_changes_sets_and_rep_ranges(self):
        strength = build_program(
            days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        hyper = build_program(
            days_per_week=4, experience="intermediate", goal="hypertrophy", bodyweight_kg=80
        )
        s_first = strength.sessions[0].exercises[0]
        h_first = hyper.sessions[0].exercises[0]
        # Strength sits in a lower rep range than hypertrophy work.
        assert s_first.rep_high <= h_first.rep_high

    def test_rest_is_longer_for_strength_than_fat_loss(self):
        strength = build_program(
            days_per_week=3, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        fat = build_program(
            days_per_week=3, experience="intermediate", goal="fat_loss", bodyweight_kg=80
        )
        s_rest = max(pe.rest_sec for s in strength.sessions for pe in s.exercises)
        f_rest = max(pe.rest_sec for s in fat.sessions for pe in s.exercises)
        assert s_rest > f_rest

    def test_no_exercise_repeats_within_a_session(self):
        """A single session must never list the same movement twice."""
        for days in (2, 3, 4, 5, 6):
            prog = build_program(
                days_per_week=days, experience="advanced", goal="hypertrophy",
                bodyweight_kg=90,
            )
            for sess in prog.sessions:
                ids = [pe.exercise_id for pe in sess.exercises]
                assert len(ids) == len(set(ids)), f"{sess.title} repeats a movement"

    def test_weekly_repeats_are_rare_and_only_from_scarce_muscles(self):
        """A movement may repeat across a week ONLY when the library offers no
        alternative. Rear delts have a single isolation option, so a 6-day split
        legitimately schedules it on both pull days. Anything beyond that means
        the builder is recycling lifts instead of selecting them."""
        from collections import Counter

        prog = build_program(
            days_per_week=6, experience="advanced", goal="hypertrophy", bodyweight_kg=90
        )
        ids = [pe.exercise_id for s in prog.sessions for pe in s.exercises]
        dups = {k: v for k, v in Counter(ids).items() if v > 1}
        assert len(dups) <= 1, f"too much recycling: {dups}"
        for ex_id in dups:
            ex = EXERCISES[ex_id]
            assert ex.kind == "isolation", "a compound was scheduled twice in one week"
            alternatives = [
                e for e in EXERCISES.values()
                if e.primary == ex.primary and e.kind == "isolation"
            ]
            assert len(alternatives) <= 2, (
                f"{ex_id} repeats but {len(alternatives)} alternatives existed"
            )

    @pytest.mark.parametrize("experience", ["beginner", "intermediate", "advanced"])
    def test_sets_respect_the_experience_volume_cap(self, experience):
        prog = build_program(
            days_per_week=4, experience=experience, goal="hypertrophy", bodyweight_kg=80
        )
        counts: dict = {}
        for sess in prog.sessions:
            for pe in sess.exercises:
                ex = EXERCISES[pe.exercise_id]
                counts[ex.primary] = counts.get(ex.primary, 0) + pe.sets
        cap = WEEKLY_SET_CAP[experience]
        for muscle, total in counts.items():
            assert total <= cap, f"{muscle} got {total} sets, cap is {cap}"

    def test_beginner_starts_lighter_than_advanced(self):
        beg = build_program(
            days_per_week=4, experience="beginner", goal="strength", bodyweight_kg=80
        )
        adv = build_program(
            days_per_week=4, experience="advanced", goal="strength", bodyweight_kg=80
        )
        b_squat = next(
            pe for s in beg.sessions for pe in s.exercises if pe.exercise_id == "back_squat"
        )
        a_squat = next(
            pe for s in adv.sessions for pe in s.exercises if pe.exercise_id == "back_squat"
        )
        assert b_squat.start_weight < a_squat.start_weight


# ---------------------------------------------------------------------------
# Determinism and projection
# ---------------------------------------------------------------------------


class TestDeterminismAndProjection:
    def test_same_inputs_produce_the_same_programme(self):
        a = build_program(days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80)
        b = build_program(days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80)
        assert a.to_dict() == b.to_dict()

    def test_projection_climbs_reps_then_adds_load(self):
        """Regression: the ladder used to walk *down* the rep range.

        It produced 60x10 -> 60x9 -> 60x8 and never got heavier. Reps must climb
        to the top of the range first, then the load steps up and resets.
        """
        prog = build_program(
            days_per_week=3, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        weeks = project_overload(prog, 7)

        def squat(week):
            return next(
                l for l in week["sessions"][0]["lifts"] if l["exercise"] == "back_squat"
            )

        reps = [squat(w)["reps"] for w in weeks]
        weights = [squat(w)["weight"] for w in weeks]
        low = squat(weeks[0])["reps"]

        # Week 1 starts at the bottom of the range.
        assert reps[0] == low
        # Reps increase before the load ever moves.
        assert reps[1] > reps[0]
        assert weights[0] == weights[1]
        # Eventually the weight steps up and reps reset to the bottom.
        assert any(w > weights[0] for w in weights)
        step_idx = next(i for i, w in enumerate(weights) if w > weights[0])
        assert reps[step_idx] == low

    def test_projection_length_matches_request(self):
        prog = build_program(days_per_week=3, experience="intermediate", goal="strength", bodyweight_kg=80)
        assert len(project_overload(prog, 5)) == 5
        assert len(project_overload(prog, 1)) == 1

    def test_projection_only_covers_compounds(self):
        prog = build_program(
            days_per_week=3, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        for week in project_overload(prog, 3):
            for sess in week["sessions"]:
                for lift in sess["lifts"]:
                    assert EXERCISES[lift["exercise"]].kind == "compound"
