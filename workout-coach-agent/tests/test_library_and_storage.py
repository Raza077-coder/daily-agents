"""Exercise library and storage tests.

The library is the data every other module reasons over, so its integrity is
worth asserting directly: unique ids, resolvable muscles and patterns, and a
sane increment for every movement. A single malformed catalog entry would
silently corrupt programme generation.
"""

from __future__ import annotations

import json

import pytest

from forge.exercises import (
    ALL_EQUIPMENT,
    DEFAULT_INCREMENTS,
    EQUIPMENT_RANK,
    EXERCISES,
    MAJOR_MUSCLES,
    MUSCLE_GROUPS,
    PATTERNS,
    filter_exercises,
    get_exercise,
    search_exercises,
)
from forge.models import SetEntry, Workout
from forge.storage import SCHEMA_VERSION, Store


# ---------------------------------------------------------------------------
# Library integrity
# ---------------------------------------------------------------------------


class TestLibraryIntegrity:
    def test_library_is_populated(self):
        assert len(EXERCISES) >= 60

    def test_ids_are_unique(self):
        ids = [e.id for e in EXERCISES.values()]
        assert len(ids) == len(set(ids))

    def test_every_primary_muscle_is_known(self):
        for e in EXERCISES.values():
            assert e.primary in MUSCLE_GROUPS, f"{e.id} has unknown primary {e.primary}"

    def test_every_secondary_muscle_is_known(self):
        for e in EXERCISES.values():
            for m in e.secondary:
                assert m in MUSCLE_GROUPS, f"{e.id} has unknown secondary {m}"

    def test_primary_is_never_duplicated_in_secondary(self):
        """Otherwise volume attribution would double-count the same muscle."""
        for e in EXERCISES.values():
            assert e.primary not in e.secondary, e.id

    def test_every_pattern_is_known(self):
        for e in EXERCISES.values():
            assert e.pattern in PATTERNS, f"{e.id} has unknown pattern {e.pattern}"

    def test_every_equipment_type_has_a_default_increment(self):
        for e in EXERCISES.values():
            assert e.equipment in DEFAULT_INCREMENTS, e.id

    def test_every_equipment_type_has_a_rank(self):
        """A missing rank would silently sort to the end of the preference list."""
        for e in EXERCISES.values():
            assert e.equipment in EQUIPMENT_RANK, e.id

    def test_bodyweight_movements_are_unloadable(self):
        for e in EXERCISES.values():
            if e.equipment == "bodyweight":
                assert e.increment == 0.0, e.id
                assert e.strength_ratio == 0.0, e.id

    def test_loaded_movements_have_a_positive_ratio(self):
        for e in EXERCISES.values():
            if e.equipment != "bodyweight":
                assert e.strength_ratio > 0, f"{e.id} would start at 0 kg"

    def test_levels_are_valid(self):
        for e in EXERCISES.values():
            assert e.level in ("beginner", "intermediate", "advanced"), e.id

    def test_kinds_are_valid(self):
        for e in EXERCISES.values():
            assert e.kind in ("compound", "isolation"), e.id

    def test_major_muscles_are_all_real(self):
        assert all(m in MUSCLE_GROUPS for m in MAJOR_MUSCLES)

    def test_all_equipment_is_derived_from_the_catalog(self):
        assert set(ALL_EQUIPMENT) == {e.equipment for e in EXERCISES.values()}

    def test_round_trip_through_dict(self):
        from forge.models import Exercise

        for e in list(EXERCISES.values())[:15]:
            assert Exercise.from_dict(e.to_dict()).to_dict() == e.to_dict()


# ---------------------------------------------------------------------------
# Lookup and search
# ---------------------------------------------------------------------------


class TestLookup:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("bench", "barbell_bench_press"),
            ("squat", "back_squat"),
            ("deadlift", "deadlift"),
            ("barbell_bench_press", "barbell_bench_press"),
            ("barbell bench press", "barbell_bench_press"),
        ],
    )
    def test_ids_aliases_and_names_resolve(self, query, expected):
        assert get_exercise(query).id == expected

    def test_hyphens_and_case_are_tolerated(self):
        assert get_exercise("Barbell-Bench-Press").id == "barbell_bench_press"

    def test_unknown_raises_with_a_helpful_message(self):
        with pytest.raises(KeyError) as exc:
            get_exercise("definitely_not_a_lift")
        assert "unknown" in str(exc.value).lower()

    def test_ambiguous_query_lists_candidates(self):
        """Ambiguity must ask rather than silently guess the wrong lift."""
        with pytest.raises(KeyError) as exc:
            get_exercise("raise")
        assert "ambiguous" in str(exc.value).lower()
        assert "lateral_raise" in str(exc.value)

    def test_common_gym_shorthands_resolve(self):
        """The most-used shorthands should just work rather than ask."""
        assert get_exercise("bench").id == "barbell_bench_press"
        assert get_exercise("curl").id == "barbell_curl"
        assert get_exercise("row").id == "barbell_row"
        assert get_exercise("press").id == "overhead_press"


class TestSearchAndFilter:
    def test_search_scores_exact_id_first(self):
        results = search_exercises("bench")
        assert results
        assert results[0].id == "barbell_bench_press"

    def test_empty_query_returns_nothing(self):
        assert search_exercises("") == []

    def test_filter_by_muscle(self):
        rows = filter_exercises(muscle="chest")
        assert rows
        assert all("chest" in e.focus_muscles for e in rows)

    def test_filter_by_equipment(self):
        rows = filter_exercises(equipment=["dumbbell"])
        assert rows
        assert all(e.equipment == "dumbbell" for e in rows)

    def test_filter_by_pattern(self):
        rows = filter_exercises(pattern="hinge")
        assert rows
        assert all(e.pattern == "hinge" for e in rows)

    def test_filter_excludes_used_ids(self):
        rows = filter_exercises(exclude=["barbell_bench_press"])
        assert "barbell_bench_press" not in {e.id for e in rows}

    def test_max_level_excludes_harder_movements(self):
        rows = filter_exercises(max_level="beginner")
        assert all(e.level == "beginner" for e in rows)

    def test_compounds_sort_before_isolation(self):
        rows = filter_exercises(pattern="hinge")
        kinds = [e.kind == "compound" for e in rows]
        assert kinds == sorted(kinds, reverse=True)

    def test_within_compounds_the_heaviest_equipment_comes_first(self):
        """The regression that put bodyweight work ahead of barbell lifts."""
        rows = filter_exercises(pattern="hinge")
        compounds = [e for e in rows if e.kind == "compound"]
        ranks = [EQUIPMENT_RANK[e.equipment] for e in compounds]
        assert ranks == sorted(ranks)
        assert compounds[0].equipment == "barbell"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TestStorage:
    def test_blank_store_has_the_expected_shape(self, store):
        data = store.load()
        assert data["schema_version"] == SCHEMA_VERSION
        assert data["workouts"] == []
        assert data["program"] is None
        assert data["profile"]["units"] == "kg"

    def test_save_then_load_round_trips(self, store):
        store.set_profile(bodyweight_kg=82.5, experience="advanced")
        assert store.bodyweight_kg == 82.5
        assert store.experience == "advanced"

    def test_reads_are_consistent_across_instances(self, tmp_path):
        path = tmp_path / "training.json"
        Store(path).set_profile(bodyweight_kg=90.0)
        assert Store(path).bodyweight_kg == 90.0

    def test_atomic_write_leaves_no_temp_files(self, store):
        store.set_profile(bodyweight_kg=80.0)
        leftovers = [p.name for p in store.path.parent.iterdir() if p.name.startswith(".training-")]
        assert leftovers == []

    def test_corrupt_file_raises_rather_than_overwriting(self, tmp_path):
        """Refusing to clobber unparseable user data is the safe failure."""
        path = tmp_path / "training.json"
        path.write_text("this is not json", encoding="utf-8")
        with pytest.raises(ValueError):
            Store(path).load()
        # The damaged file is still on disk, untouched.
        assert path.read_text(encoding="utf-8") == "this is not json"

    def test_json_array_file_is_rejected(self, tmp_path):
        path = tmp_path / "training.json"
        path.write_text("[1,2,3]", encoding="utf-8")
        with pytest.raises(ValueError):
            Store(path).load()

    def test_missing_keys_are_backfilled_with_defaults(self, tmp_path):
        path = tmp_path / "training.json"
        path.write_text(json.dumps({"profile": {"bodyweight_kg": 71}}), encoding="utf-8")
        store = Store(path)
        assert store.bodyweight_kg == 71.0
        assert store.load()["workouts"] == []

    def test_reset_wipes_everything(self, engine):
        engine.log("bench", "60x8x3")
        engine.store.reset()
        assert engine.store.workouts == []
        assert engine.store.get_program() is None

    def test_add_workout_keeps_chronological_order(self, store):
        from forge.models import LoggedExercise

        for day in ("2026-01-09", "2026-01-05", "2026-01-07"):
            store.add_workout(
                Workout(
                    date=day,
                    entries=[LoggedExercise(exercise_id="barbell_bench_press",
                                            sets=[SetEntry(60, 8)])],
                )
            )
        dates = [w.date for w in store.workouts]
        assert dates == sorted(dates)

    def test_remove_workout_returns_true_only_when_something_was_removed(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        assert engine.store.remove_workout("2026-01-05") is True
        assert engine.store.remove_workout("2026-01-05") is False

    def test_exercise_history_is_chronological(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-09")
        engine.log("bench", "60x8x3", day="2026-01-05")
        hist = engine.store.exercise_history("barbell_bench_press")
        assert [h["date"] for h in hist] == ["2026-01-05", "2026-01-09"]

    def test_last_workout_can_filter_by_exercise(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        engine.log("squat", "80x5x3", day="2026-01-07")
        assert engine.store.last_workout("barbell_bench_press").date == "2026-01-05"
        assert engine.store.last_workout().date == "2026-01-07"

    def test_program_round_trips_through_disk(self, engine):
        from forge.program import build_program

        prog = build_program(
            days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        engine.store.set_program(prog)
        reloaded = Store(engine.store.path).get_program()
        assert reloaded.to_dict() == prog.to_dict()
