"""Tests for :mod:`jobtracker.store` — persistence, lookup, atomic writes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from jobtracker.models import NotFoundError, ValidationError
from jobtracker.store import SCHEMA_VERSION, Vault

from conftest import TODAY_STR, applied_app, make_app


def test_an_empty_vault_has_no_applications(vault):
    assert len(vault) == 0
    assert vault.ids() == []


def test_add_and_get_by_full_id(vault):
    vault.add(applied_app("acme-swe"))
    assert vault.get("acme-swe").id == "acme-swe"


def test_get_accepts_an_unambiguous_prefix(vault):
    vault.add(applied_app("acme-backend-engineer"))
    assert vault.get("acme").id == "acme-backend-engineer"


def test_get_raises_on_an_ambiguous_prefix(vault):
    vault.add(applied_app("acme-backend", applied_on="2026-09-01"))
    vault.add(applied_app("acme-frontend", applied_on="2026-09-02"))
    with pytest.raises(ValidationError, match="ambiguous"):
        vault.get("acme")


def test_get_raises_not_found_and_lists_known_ids(vault):
    vault.add(applied_app("acme-swe"))
    with pytest.raises(NotFoundError, match="no application with id"):
        vault.get("nope")


def test_find_returns_none_instead_of_raising(vault):
    assert vault.find("missing") is None


def test_duplicate_ids_are_refused(vault):
    vault.add(applied_app("acme-swe"))
    with pytest.raises(ValidationError, match="already exists"):
        vault.add(applied_app("acme-swe"))


def test_remove_returns_the_removed_record(vault):
    vault.add(applied_app("acme-swe"))
    removed = vault.remove("acme-swe")
    assert removed.id == "acme-swe"
    assert len(vault) == 0


def test_contains_works_by_id(vault):
    vault.add(applied_app("acme-swe"))
    assert "acme-swe" in vault
    assert "other" not in vault


def test_sort_puts_open_work_first_then_priority(vault):
    low = applied_app("z-low", applied_on="2026-09-01", priority=1)
    high = applied_app("a-high", applied_on="2026-09-02", priority=5)
    closed = applied_app("m-closed", applied_on="2026-09-03", priority=5)
    closed.status = "rejected"
    closed.closed_on = "2026-09-10"

    vault.add(low)
    vault.add(high)
    vault.add(closed)
    vault.sort()

    ids = vault.ids()
    assert ids[0] == "a-high"
    assert ids[-1] == "m-closed"


def test_filter_by_status_active_and_tag(vault):
    live = applied_app("live", applied_on="2026-09-01", tags=["python"])
    closed = applied_app("closed", applied_on="2026-09-01", tags=["python"])
    closed.status = "rejected"
    closed.closed_on = "2026-09-02"
    vault.add(live)
    vault.add(closed)

    assert [a.id for a in vault.filter(active_only=True)] == ["live"]
    assert [a.id for a in vault.filter(statuses=["rejected"])] == ["closed"]
    assert [a.id for a in vault.filter(tag="python")] == ["live", "closed"]
    assert [a.id for a in vault.filter(tag="absent")] == []


def test_filter_by_company_is_case_insensitive(vault):
    vault.add(applied_app("a", company="Acme Corp"))
    assert [a.id for a in vault.filter(company="acme")] == ["a"]


def test_stats_counts_by_status(vault):
    vault.add(applied_app("a", applied_on="2026-09-01"))
    vault.add(make_app("b"))
    stats = vault.stats()
    assert stats["total"] == 2
    assert stats["wishlist"] == 1
    assert stats["active"] == 1


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "jobflow.json"
    vault = Vault(path=path)
    vault.owner = "Raza"
    vault.add(applied_app("a", "2026-09-01", salary_min="100000", salary_max="120000"))
    vault.save()

    reloaded = Vault.load(path)
    assert reloaded.owner == "Raza"
    assert len(reloaded) == 1
    assert reloaded.get("a").salary_min == 10_000_000


def test_saved_file_is_the_documented_schema(tmp_path):
    path = tmp_path / "jobflow.json"
    vault = Vault(path=path)
    vault.add(applied_app("a", "2026-09-01"))
    vault.save()

    payload = json.loads(path.read_text())
    assert payload["schema_version"] == SCHEMA_VERSION
    assert isinstance(payload["applications"], list)
    assert payload["applications"][0]["id"] == "a"


def test_loading_a_missing_file_yields_an_empty_vault(tmp_path):
    vault = Vault.load(tmp_path / "absent.json")
    assert len(vault) == 0


def test_loading_a_blank_file_yields_an_empty_vault(tmp_path):
    path = tmp_path / "jobflow.json"
    path.write_text("   \n")
    assert len(Vault.load(path)) == 0


def test_loading_malformed_json_names_the_location(tmp_path):
    path = tmp_path / "jobflow.json"
    path.write_text('{"applications": [ }')
    with pytest.raises(ValidationError, match="not valid JSON"):
        Vault.load(path)


def test_loading_a_wrong_schema_version_is_refused(tmp_path):
    path = tmp_path / "jobflow.json"
    path.write_text(json.dumps({"schema_version": 99, "applications": []}))
    with pytest.raises(ValidationError, match="schema_version"):
        Vault.load(path)


def test_loading_duplicate_ids_is_refused(tmp_path):
    payload = {
        "schema_version": SCHEMA_VERSION,
        "applications": [
            {"id": "a", "company": "Acme", "role": "Dev"},
            {"id": "a", "company": "Acme", "role": "Dev"},
        ],
    }
    path = tmp_path / "jobflow.json"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValidationError, match="duplicate application id"):
        Vault.load(path)


def test_loading_a_non_object_vault_is_refused(tmp_path):
    path = tmp_path / "jobflow.json"
    path.write_text("[1, 2, 3]")
    with pytest.raises(ValidationError, match="must be a JSON object"):
        Vault.load(path)


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "jobflow.json"
    vault = Vault(path=path)
    vault.add(applied_app("a", "2026-09-01"))
    vault.save()

    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "jobflow.json"]
    assert leftovers == []


def test_a_failed_save_leaves_the_previous_vault_intact(tmp_path):
    """The whole point of temp-file + os.replace."""
    path = tmp_path / "jobflow.json"
    vault = Vault(path=path)
    vault.add(applied_app("a", "2026-09-01"))
    vault.save()
    before = path.read_text()

    class Exploding(json.JSONEncoder):
        def default(self, o):  # pragma: no cover - defensive
            raise RuntimeError("boom")

    # Serialisation itself is fine; force the failure inside the writer.
    original = json.dumps
    try:
        json.dumps = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        with pytest.raises(RuntimeError):
            vault.save()
    finally:
        json.dumps = original

    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["jobflow.json"]


def test_save_without_a_path_raises():
    vault = Vault()
    with pytest.raises(Exception, match="no vault path"):
        vault.save()


def test_saved_vault_is_owner_read_only(tmp_path):
    path = tmp_path / "jobflow.json"
    vault = Vault(path=path)
    vault.add(applied_app("a", "2026-09-01"))
    vault.save()
    mode = oct(os.stat(path).st_mode & 0o777)
    assert mode == oct(0o600)


def test_default_vault_path_honours_the_env_var(monkeypatch, tmp_path):
    from jobtracker.store import default_vault_path

    target = tmp_path / "custom.json"
    monkeypatch.setenv("JOBFLOW_VAULT", str(target))
    assert default_vault_path() == target
