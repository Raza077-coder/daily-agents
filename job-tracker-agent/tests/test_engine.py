"""Tests for :mod:`jobtracker.engine` — the facade every surface talks to."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from jobtracker.engine import DEFAULT_CONFIG, JobFlowEngine
from jobtracker.models import STAGES, ValidationError
from jobtracker.store import Vault

from conftest import TODAY, applied_app, make_app


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


# --------------------------------------------------------------------------
# Opening & plumbing
# --------------------------------------------------------------------------


def test_open_creates_the_vault_file_on_first_save(tmp_path):
    path = tmp_path / "jobflow.json"
    engine = JobFlowEngine.open(path)
    assert engine.vault.path == path
    engine.add("Acme", "Dev", today=TODAY)
    engine.save()
    assert path.exists()


def test_open_honours_the_env_var(monkeypatch, tmp_path):
    target = tmp_path / "from-env.json"
    monkeypatch.setenv("JOBFLOW_VAULT", str(target))
    engine = JobFlowEngine.open()
    assert engine.path == target


def test_from_dict_builds_an_in_memory_engine():
    data = {"schema_version": 1, "owner": "x", "applications": []}
    engine = JobFlowEngine.from_dict(data)
    assert engine.path is None
    assert len(engine.vault) == 0


def test_default_config_is_the_documented_set():
    assert DEFAULT_CONFIG["weekly_target"] == 5
    # None means "use the per-status STALL_DAYS thresholds".
    assert DEFAULT_CONFIG["followup_days"] is None
    assert DEFAULT_CONFIG["second_followup_days"] == 14
    assert DEFAULT_CONFIG["wishlist_idle_days"] == 10


def test_config_overrides_are_merged(engine):
    merged = JobFlowEngine(vault=engine.vault, config={"weekly_target": 9})
    assert merged.config["weekly_target"] == 9
    assert merged.config["followup_days"] == DEFAULT_CONFIG["followup_days"]


def test_none_config_values_are_ignored(engine):
    merged = JobFlowEngine(vault=engine.vault, config={"weekly_target": None})
    assert merged.config["weekly_target"] == DEFAULT_CONFIG["weekly_target"]


# --------------------------------------------------------------------------
# resolve_today
# --------------------------------------------------------------------------


def test_resolve_today_uses_an_explicit_date(engine):
    assert engine.resolve_today("2026-09-17") == TODAY


def test_resolve_today_falls_back_to_the_clock(engine):
    assert engine.resolve_today(None) == date.today()
    assert engine.resolve_today("") == date.today()


def test_resolve_today_rejects_a_malformed_date(engine):
    with pytest.raises(ValidationError):
        engine.resolve_today("17-09-2026")


# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------


def test_vocabulary_exposes_the_whole_contract():
    vocab = JobFlowEngine.vocabulary()
    assert vocab["stages"] == list(STAGES)
    assert "wishlist" in vocab["statuses"]
    assert "linkedin" in vocab["sources"]
    assert vocab["stage_index"]["applied"] == 0
    assert "stalled" in vocab["rules"]
    assert set(vocab["severities"]) == {"critical", "high", "medium", "low"}
    assert vocab["rule_labels"]["stalled"] == "Going stale"


# --------------------------------------------------------------------------
# Writes delegate correctly
# --------------------------------------------------------------------------


def test_engine_add_delegates_to_the_pipeline(engine):
    app = engine.add("Acme", "Dev", today=TODAY)
    assert engine.get(app.id).id == app.id


def test_engine_move_refuses_a_skipped_rung(engine):
    app = engine.add("Acme", "Dev", status="applied", today=TODAY)
    with pytest.raises(ValidationError, match="skips"):
        engine.move(app.id, "onsite", today=TODAY)


def test_engine_drop_removes_it(engine):
    app = engine.add("Acme", "Dev", today=TODAY)
    engine.drop(app.id)
    assert len(engine.vault) == 0


def test_engine_list_filters(engine):
    engine.add("Acme", "Dev", status="applied", today=TODAY)
    engine.add("Globex", "SRE", today=TODAY)
    assert len(engine.list(active_only=True)) == 1


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------


def test_summary_and_funnel_run_on_an_empty_vault(engine):
    """Regression: this used to raise because resolve_today(None) blew up."""
    assert engine.summary(today=TODAY)["totals"]["applications"] == 0
    assert engine.funnel(today=TODAY)["submitted"] == 0


def test_plan_runs_on_an_empty_vault(engine):
    assert engine.plan(today=TODAY)["total"] >= 1


def test_engine_render_methods_all_return_text(engine):
    engine.add("Acme", "Dev", status="applied", today=TODAY)
    assert engine.render_status(today=TODAY).strip()
    assert engine.render_board(today=TODAY).strip()
    assert engine.render_plan(today=TODAY).strip()
    assert engine.render_report(today=TODAY).strip()
    assert engine.render_markdown(today=TODAY).strip()


def test_export_returns_a_serialisable_bundle(engine):
    engine.add("Acme", "Dev", status="applied", today=TODAY)
    bundle = engine.export(today=TODAY)
    assert bundle["generated_on"] == TODAY.isoformat()


# --------------------------------------------------------------------------
# Upcoming interviews
# --------------------------------------------------------------------------


def test_upcoming_lists_interviews_within_the_horizon(engine):
    app = engine.add("Acme", "Dev", status="applied", today=TODAY)
    engine.schedule_interview(app.id, (TODAY + timedelta(days=3)).isoformat(), kind="technical")
    engine.schedule_interview(app.id, (TODAY + timedelta(days=40)).isoformat(), kind="final")

    rows = engine.upcoming(today=TODAY, days=14)
    assert len(rows) == 1
    assert rows[0]["days_away"] == 3


def test_upcoming_reports_days_away_and_the_contact(engine):
    app = engine.add("Acme", "Dev", status="applied", today=TODAY, contact="marta@acme.example")
    engine.schedule_interview(app.id, (TODAY + timedelta(days=1)).isoformat(), kind="screen")

    row = engine.upcoming(today=TODAY)[0]
    assert row["days_away"] == 1
    assert row["contact"] == "marta@acme.example"
    assert row["label"] == "Acme — Dev"


def test_upcoming_excludes_completed_interviews(engine):
    app = engine.add("Acme", "Dev", status="applied", today=TODAY)
    engine.schedule_interview(app.id, (TODAY + timedelta(days=2)).isoformat(), kind="screen")
    engine.complete_interview(app.id, on=(TODAY + timedelta(days=2)).isoformat(), today=TODAY)
    assert engine.upcoming(today=TODAY) == []


def test_upcoming_excludes_past_interviews(engine):
    app = engine.add(
        "Acme", "Dev", status="applied", created_on="2026-08-01",
        applied_on="2026-08-01", today=TODAY,
    )
    engine.schedule_interview(app.id, days_ago(2), kind="screen")
    assert engine.upcoming(today=TODAY) == []


def test_upcoming_is_sorted_by_date(engine):
    a = engine.add("Acme", "Dev", status="applied", today=TODAY)
    b = engine.add("Globex", "SRE", status="applied", today=TODAY)
    engine.schedule_interview(a.id, (TODAY + timedelta(days=5)).isoformat(), kind="final")
    engine.schedule_interview(b.id, (TODAY + timedelta(days=1)).isoformat(), kind="screen")

    dates = [r["on"] for r in engine.upcoming(today=TODAY)]
    assert dates == sorted(dates)


# --------------------------------------------------------------------------
# Demo seeding
# --------------------------------------------------------------------------


def test_seed_demo_populates_a_realistic_vault(engine):
    info = engine.seed_demo(today=TODAY, force=True)
    assert info["seeded"] == 7
    assert info["today"] == TODAY.isoformat()


def test_seed_demo_includes_the_hard_cases(engine):
    engine.seed_demo(today=TODAY, force=True)
    ids = engine.vault.ids()

    cobalt = engine.get([i for i in ids if i.startswith("cobalt")][0])
    assert cobalt.status == "rejected"
    assert cobalt.furthest_stage() == "onsite"

    offer = [a for a in engine.vault if a.status == "offer"]
    assert offer, "the demo must contain a live offer"


def test_seed_demo_refuses_to_overwrite_without_force(engine):
    engine.seed_demo(today=TODAY, force=True)
    with pytest.raises(ValidationError, match="not empty"):
        engine.seed_demo(today=TODAY)


def test_seed_demo_with_force_replaces_the_contents(engine):
    engine.add("Manual", "Entry", today=TODAY)
    engine.seed_demo(today=TODAY, force=True)
    assert "manual-entry" not in engine.vault.ids()


def test_seed_demo_is_deterministic(engine):
    first = engine.seed_demo(today=TODAY, force=True)["seeded"]
    snapshot = engine.vault.to_dict()
    second = engine.seed_demo(today=TODAY, force=True)["seeded"]

    assert first == second
    assert engine.vault.to_dict() == snapshot


def test_seeded_vault_survives_a_save_and_reload(tmp_path):
    path = tmp_path / "jobflow.json"
    engine = JobFlowEngine.open(path)
    engine.seed_demo(today=TODAY, force=True)
    engine.save()

    reloaded = JobFlowEngine.open(path)
    assert len(reloaded.vault) == 7
    assert reloaded.summary(today=TODAY) == engine.summary(today=TODAY)


def test_seeded_vault_produces_a_populated_plan(engine):
    engine.seed_demo(today=TODAY, force=True)
    data = engine.plan(today=TODAY)
    assert data["total"] > 0
    assert data["actions"]


def test_seeded_vault_reports_a_fresh_application_as_not_yet_measurable(engine):
    """The demo deliberately contains an application that is too young."""
    engine.seed_demo(today=TODAY, force=True)
    summary = engine.summary(today=TODAY)
    funnel_data = summary["funnel"]
    assert funnel_data["response_denominator"] < funnel_data["submitted"]


def test_seeded_vault_has_a_stalled_and_a_wishlist_row(engine):
    engine.seed_demo(today=TODAY, force=True)
    assert engine.vault.filter(statuses=["wishlist"])
    assert engine.vault.filter(tag="ml") or engine.vault.filter(tag="spark")
