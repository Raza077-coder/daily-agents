"""Coach voice tests.

The coach is the layer users actually read, so the tests check two things: that
phrasing survives a missing or partial persona file, and that the output obeys
the persona's own rules (disclaimer present, banned phrases absent).
"""

from __future__ import annotations

import json

import pytest

from forge.analytics import summary
from forge.coach import DEFAULT_PERSONA_PATH, Coach, load_persona
from forge.program import build_program


class TestPersonaLoading:
    def test_real_persona_file_loads(self):
        p = load_persona()
        assert p["name"]
        assert p["messages"]
        assert p["safety"]["disclaimer"]

    def test_missing_file_falls_back_inline(self, tmp_path):
        """A missing persona must not silence the coach entirely."""
        p = load_persona(tmp_path / "nope.json")
        assert p["name"]
        assert p["safety"]["disclaimer"]

    def test_corrupt_json_falls_back_instead_of_raising(self, tmp_path):
        bad = tmp_path / "broken.json"
        bad.write_text("{not valid json at all", encoding="utf-8")
        p = load_persona(bad)
        assert p["name"]

    def test_partial_persona_is_merged_over_defaults(self, tmp_path):
        part = tmp_path / "partial.json"
        part.write_text(json.dumps({"name": "TINY"}), encoding="utf-8")
        p = load_persona(part)
        assert p["name"] == "TINY"
        # Untouched sections survive from the default.
        assert p["safety"]["disclaimer"]

    def test_disclaimer_matches_the_file(self):
        raw = json.loads(DEFAULT_PERSONA_PATH.read_text(encoding="utf-8"))
        assert Coach().disclaimer == raw["safety"]["disclaimer"]

    def test_unknown_message_key_returns_empty_not_an_error(self):
        assert Coach().msg("definitely_not_a_key") == ""

    def test_message_template_with_a_missing_field_does_not_raise(self):
        """Templates are edited by hand, so a typo must degrade gracefully."""
        c = Coach(persona={"messages": {"x": "Hello {name} {missing_field}"}})
        assert c.msg("x", name="Ali") == "Hello {name} {missing_field}"


class TestPrescriptionPhrasing:
    def test_increase_names_both_the_new_and_old_load(self, engine):
        engine.log("bench", "60x12x3")
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        text = Coach().describe_prescription(advice)
        assert "62.5kg" in text and "60kg" in text

    def test_bodyweight_is_named_not_shown_as_zero_kilos(self, engine):
        engine.log("pullup", "bw x10x3")
        advice = engine.next_prescription("pullup", 8, 15, 3)
        text = Coach().describe_prescription(advice)
        assert "bodyweight" in text
        assert "0kg" not in text

    def test_start_action_says_so(self, engine):
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert "to start" in Coach().describe_prescription(advice)

    def test_deload_is_labelled_a_deload(self, engine):
        for day in ("2026-01-05", "2026-01-07", "2026-01-09"):
            engine.log("bench", "60x6x2", day=day)
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert "deload" in Coach().describe_prescription(advice).lower()

    def test_prescription_includes_the_reasoning(self, engine):
        engine.log("bench", "60x8x3")
        advice = engine.next_prescription("barbell_bench_press", 8, 12, 3)
        assert advice["reason"][:20] in Coach().describe_prescription(advice)


class TestReports:
    def test_summary_report_renders_for_empty_history(self):
        text = Coach().describe_summary(summary([], "2026-01-05", 28))
        assert "FORGE" in text
        assert "No sessions logged yet" in text

    def test_summary_report_includes_tonnage_and_streak(self, engine):
        for day in ("2026-01-05", "2026-01-07", "2026-01-09"):
            engine.log("bench", "60x8x3", day=day)
        data = summary(engine.store.workouts, "2026-01-09", 28)
        text = Coach().describe_summary(data)
        assert "Lifetime" in text
        assert "Streak" in text
        assert "Volume by muscle" in text

    def test_coverage_line_counts_all_thirteen_muscles(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        data = summary(engine.store.workouts, "2026-01-05", 28)
        text = Coach().describe_summary(data)
        assert "13 major muscles" in text

    def test_program_report_always_carries_the_disclaimer(self):
        prog = build_program(
            days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        text = Coach().describe_program(prog)
        assert Coach().disclaimer in text
        assert "4x" in text  # sets x reps actually listed

    def test_session_report_lists_each_exercise(self, engine):
        result = engine.log_session(
            day="2026-01-05", session="Upper A",
            entries={"bench": "60x8x3", "row": "50x10x3"},
        )
        text = Coach().describe_session(result)
        assert "Barbell Bench Press" in text
        assert "Barbell Row" in text

    def test_progress_report_shows_gain(self, engine):
        engine.log("bench", "60x8x3", day="2026-01-05")
        engine.log("bench", "70x8x3", day="2026-01-12")
        text = Coach().describe_progress(engine.progress("barbell_bench_press"))
        assert "+10" in text
        assert "improving" in text

    def test_today_report_handles_a_rest_day(self, engine):
        engine.store.set_program(
            build_program(days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80)
        )
        text = Coach().describe_today(engine.today("2026-01-11"))  # a Sunday
        assert "Rest day" in text

    def test_projection_report_explains_the_caveat(self):
        prog = build_program(
            days_per_week=3, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        from forge.program import project_overload

        text = Coach().describe_projection(project_overload(prog, 3))
        assert "Week 1" in text
        assert "missed sessions" in text.lower()


class TestVoiceRules:
    def test_no_banned_phrase_appears_in_any_report(self, engine):
        """The persona bans vague advice; the code must not contradict it."""
        banned = [p.lower() for p in load_persona()["voice"]["banned_phrases"]]
        for day in ("2026-01-05", "2026-01-07", "2026-01-09"):
            engine.log("bench", "60x8x3", day=day)
        coach = Coach()
        prog = build_program(
            days_per_week=4, experience="intermediate", goal="strength", bodyweight_kg=80
        )
        blobs = [
            coach.describe_summary(summary(engine.store.workouts, "2026-01-09", 28)),
            coach.describe_program(prog),
            coach.describe_prescription(engine.next_prescription("barbell_bench_press", 8, 12, 3)),
        ]
        for text in blobs:
            low = text.lower()
            for phrase in banned:
                assert phrase not in low, f"banned phrase {phrase!r} leaked into output"

    def test_no_exclamation_marks_in_plain_reports(self, engine):
        """The persona is calm; hype punctuation is out of character."""
        for day in ("2026-01-05", "2026-01-07"):
            engine.log("bench", "60x8x3", day=day)
        text = Coach().describe_summary(summary(engine.store.workouts, "2026-01-07", 28))
        assert "!" not in text

    def test_greeting_names_the_coach(self):
        assert "FORGE" in Coach().greet()
