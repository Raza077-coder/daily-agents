"""Engine tests: lifecycle, CRUD, search, generation, import/export."""

from __future__ import annotations

import json
import os

import pytest

from vaultguard.engine import (
    SearchQuery,
    VaultAuthError,
    VaultEngine,
    VaultError,
    temp_vault_path,
)
from vaultguard.storage import VaultFormatError

from .conftest import FAST_ITERATIONS, TEST_MASTER


class TestLifecycle:
    def test_create_then_unlock_reports_entry_count(self, vault_path):
        engine = VaultEngine(vault_path, iterations=FAST_ITERATIONS)
        assert engine.exists() is False
        created = engine.create(TEST_MASTER)
        assert created["status"] == "created"
        assert engine.is_unlocked

        reopened = VaultEngine(vault_path, iterations=FAST_ITERATIONS)
        assert reopened.is_unlocked is False
        result = reopened.unlock(TEST_MASTER)
        assert result["status"] == "unlocked"
        assert result["entries"] == 0

    def test_create_refuses_to_overwrite_without_force(self, engine, vault_path):
        with pytest.raises(VaultError):
            VaultEngine(vault_path, iterations=FAST_ITERATIONS).create(TEST_MASTER)
        forced = VaultEngine(vault_path, iterations=FAST_ITERATIONS).create(TEST_MASTER, force=True)
        assert forced["status"] == "created"

    def test_short_master_password_is_rejected(self, vault_path):
        with pytest.raises(VaultError):
            VaultEngine(vault_path, iterations=FAST_ITERATIONS).create("short")

    def test_wrong_master_password_raises_auth_error(self, engine, vault_path):
        with pytest.raises(VaultAuthError):
            VaultEngine(vault_path, iterations=FAST_ITERATIONS).unlock("wrong-password")

    def test_locked_vault_denies_access(self, vault_path):
        engine = VaultEngine(vault_path, iterations=FAST_ITERATIONS)
        engine.create(TEST_MASTER)
        engine.lock()
        assert engine.is_unlocked is False
        with pytest.raises(VaultAuthError):
            _ = engine.entries

    def test_missing_vault_raises_on_unlock(self, vault_path):
        with pytest.raises(VaultFormatError):
            VaultEngine(vault_path, iterations=FAST_ITERATIONS).unlock(TEST_MASTER)

    def test_rekey_changes_the_password(self, engine, vault_path):
        engine.add(title="A", password="secret-password-1", save=True)
        engine.change_master_password(TEST_MASTER, "new-master-password")
        reopened = VaultEngine(vault_path)
        with pytest.raises(VaultAuthError):
            reopened.unlock(TEST_MASTER)
        assert reopened.unlock("new-master-password")["entries"] == 1

    def test_rekey_requires_a_long_enough_password(self, engine):
        with pytest.raises(VaultError):
            engine.change_master_password(TEST_MASTER, "short")

    def test_rekey_rejects_a_wrong_current_password(self, engine):
        with pytest.raises(VaultAuthError):
            engine.change_master_password("not-the-password", "another-long-password")

    def test_context_manager_locks_on_exit(self, vault_path):
        with VaultEngine(vault_path, iterations=FAST_ITERATIONS) as engine:
            engine.create(TEST_MASTER)
            assert engine.is_unlocked
        assert engine.is_unlocked is False

    def test_info_works_while_locked(self, engine, vault_path):
        engine.lock()
        info = VaultEngine(vault_path).info()
        assert info["status"] == "locked"
        assert info["iterations"] == FAST_ITERATIONS

    def test_info_reports_missing_vault(self, vault_path):
        assert VaultEngine(vault_path).info()["status"] == "missing"

    def test_persisted_after_reopen(self, engine, vault_path):
        engine.add(title="Persisted", password="a-very-good-password-9!")
        engine.lock()
        reopened = VaultEngine(vault_path)
        reopened.unlock(TEST_MASTER)
        assert reopened.list_all()[0]["title"] == "Persisted"

    def test_destroy_requires_confirmation(self, engine):
        with pytest.raises(VaultError):
            engine.destroy()

    def test_destroy_removes_the_file(self, engine, vault_path):
        engine.destroy(confirm=True)
        assert os.path.exists(vault_path) is False

    def test_temp_vault_path_is_unused(self):
        path = temp_vault_path()
        assert not os.path.exists(path)


class TestCrud:
    def test_add_returns_a_masked_entry(self, engine):
        result = engine.add(title="GitHub", password="Sup3r#Secret!")
        assert result["entry"]["title"] == "GitHub"
        assert "Sup3r#Secret!" not in json.dumps(result)

    def test_add_with_generation(self, engine):
        result = engine.add(title="Generated", generate=True, length=24)
        assert result["generated"] is True
        entry = engine.get(engine.list_all()[0]["id"], reveal=True)
        assert len(entry["password"]) == 24

    def test_add_persists_immediately(self, engine, vault_path):
        engine.add(title="Now", password="a-good-password-here-7!")
        engine.lock()
        reopened = VaultEngine(vault_path)
        reopened.unlock(TEST_MASTER)
        assert len(reopened.list_all()) == 1

    def test_get_masks_by_default(self, engine):
        added = engine.add(title="A", password="TopSecretValue123!")
        masked = engine.get(added["entry"]["id"])
        assert "TopSecretValue123!" not in json.dumps(masked)
        assert "•" in masked["password"]

    def test_reveal_returns_plaintext(self, engine):
        added = engine.add(title="A", password="TopSecretValue123!")
        secret = engine.reveal(added["entry"]["id"])
        assert secret["password"] == "TopSecretValue123!"

    def test_update_changes_a_field(self, engine):
        added = engine.add(title="Old", password="a-good-password-here-7!")
        updated = engine.update(added["entry"]["id"], title="New")
        assert updated["entry"]["title"] == "New"

    def test_update_rotating_password_marks_it_changed(self, engine):
        added = engine.add(title="A", password="a-good-password-here-7!")
        result = engine.update(added["entry"]["id"], password="a-different-password-8!")
        assert result["password_changed"] is True
        assert engine.reveal(added["entry"]["id"])["password"] == "a-different-password-8!"

    def test_update_without_changes_keeps_password_flag_false(self, engine):
        added = engine.add(title="A", password="a-good-password-here-7!")
        assert engine.update(added["entry"]["id"], notes="hi")["password_changed"] is False

    def test_update_persists(self, engine, vault_path):
        added = engine.add(title="A", password="a-good-password-here-7!")
        engine.update(added["entry"]["id"], title="Renamed")
        engine.lock()
        reopened = VaultEngine(vault_path)
        reopened.unlock(TEST_MASTER)
        assert reopened.list_all()[0]["title"] == "Renamed"

    def test_delete_removes_the_entry(self, engine):
        added = engine.add(title="Gone", password="a-good-password-here-7!")
        engine.delete(added["entry"]["id"])
        assert engine.list_all() == []

    def test_unknown_id_raises(self, engine):
        with pytest.raises(VaultError):
            engine.get("nonexistent")

    def test_lookup_by_title_works(self, engine):
        engine.add(title="ByTitle", password="a-good-password-here-7!")
        assert engine.get("ByTitle")["title"] == "ByTitle"

    def test_tags_are_normalised(self, engine):
        added = engine.add(title="A", password="a-good-password-here-7!", tags=["Work", "work", "DEV"])
        assert added["entry"]["tags"] == ["dev", "work"]

    def test_favorite_flag(self, engine):
        added = engine.add(title="Fav", password="a-good-password-here-7!", favorite=True)
        assert added["entry"]["favorite"] is True


class TestSearch:
    @pytest.fixture()
    def populated(self, engine):
        engine.add(title="GitHub", username="raza", category="work", tags=["dev"], password="Xk9#mQ2vLp7$Zw4Rt8Nb", save=False)
        engine.add(title="Gmail", username="me", category="email", password="Tz5!wR8qYb3@Kd1Hn6Mv2Pc9&Ls4", save=False)
        engine.add(title="Router", username="admin", category="wifi", password="qwerty123", save=False)
        engine.save()
        return engine

    def test_text_search_matches_titles(self, populated):
        assert [e["title"] for e in populated.search(text="git")] == ["GitHub"]

    def test_text_search_matches_usernames(self, populated):
        assert [e["title"] for e in populated.search(text="admin")] == ["Router"]

    def test_category_filter(self, populated):
        assert [e["title"] for e in populated.search(category="email")] == ["Gmail"]

    def test_tag_filter(self, populated):
        assert [e["title"] for e in populated.search(tag="dev")] == ["GitHub"]

    def test_weak_only_filter(self, populated):
        titles = [e["title"] for e in populated.search(weak_only=True)]
        assert titles == ["Router"]

    def test_limit(self, populated):
        assert len(populated.search(limit=2)) == 2

    def test_sort_by_title_descending(self, populated):
        titles = [e["title"] for e in populated.search(sort="title", descending=True)]
        assert titles == sorted(titles, reverse=True)

    def test_sort_by_age(self, populated):
        assert len(populated.search(sort="age")) == 3

    def test_query_object_works(self, populated):
        assert len(populated.search(SearchQuery(category="work"))) == 1

    def test_results_are_masked(self, populated):
        assert "a-good-password-here-7!" not in json.dumps(populated.search())

    def test_empty_query_returns_everything(self, populated):
        assert len(populated.search()) == 3


class TestAnalysis:
    def test_stats_aggregates(self, seeded):
        data = seeded.stats()
        assert data["total"] == 4
        assert data["favorites"] == 1
        assert data["with_totp"] == 1
        assert "work" in data["categories"]

    def test_duplicates_detects_the_reused_password(self, seeded):
        groups = seeded.duplicates()
        assert len(groups) == 1
        assert groups[0]["count"] == 2

    def test_no_duplicates_in_a_clean_vault(self, engine):
        engine.add(title="A", password="a-good-password-here-7!")
        assert engine.duplicates() == []

    def test_audit_scores_the_seeded_vault(self, seeded):
        report = seeded.audit()
        assert 0 <= report.score <= 100
        assert report.total_entries == 4

    def test_health_score_matches_audit(self, seeded):
        assert seeded.health_score() == seeded.audit().score

    def test_suggest_returns_a_scored_password(self, engine):
        result = engine.suggest(18)
        assert len(result["password"]) == 18
        assert result["strength"]["score"] > 0

    def test_generate_passphrase(self, engine):
        result = engine.generate("passphrase", words=5)
        assert result["kind"] == "passphrase"
        assert len(result["secret"].split("-")) >= 5

    def test_generate_pin(self, engine):
        assert engine.generate("pin", length=6)["secret"].isdigit()

    def test_generate_password_default_kind(self, engine):
        assert len(engine.generate(length=22)["secret"]) == 22

    def test_check_scores_without_storing(self, engine):
        before = len(engine.list_all())
        report = engine.check("qwerty123")
        assert report["score"] < 55
        assert len(engine.list_all()) == before

    def test_seeded_vault_has_findings(self, seeded):
        assert seeded.audit().findings


class TestImportExport:
    def test_export_then_import_round_trip(self, engine, tmp_path):
        engine.add(title="Original", password="a-good-password-here-7!")
        destination = str(tmp_path / "export.json")
        engine.export_plain(destination)

        fresh_path = str(tmp_path / "fresh.json")
        fresh = VaultEngine(fresh_path, iterations=FAST_ITERATIONS)
        fresh.create(TEST_MASTER)
        result = fresh.import_plain(destination)
        assert result["added"] == 1
        assert fresh.list_all()[0]["title"] == "Original"

    def test_export_contains_plaintext_by_design(self, engine, tmp_path):
        engine.add(title="A", password="a-good-password-here-7!")
        destination = str(tmp_path / "export.json")
        engine.export_plain(destination)
        with open(destination, encoding="utf-8") as handle:
            assert "a-good-password-here-7!" in handle.read()

    def test_masked_export_hides_passwords(self, engine, tmp_path):
        engine.add(title="A", password="a-good-password-here-7!")
        destination = str(tmp_path / "masked.json")
        engine.export_plain(destination, include_passwords=False)
        with open(destination, encoding="utf-8") as handle:
            assert "a-good-password-here-7!" not in handle.read()

    def test_export_file_is_owner_only(self, engine, tmp_path):
        destination = str(tmp_path / "export.json")
        engine.export_plain(destination)
        assert os.stat(destination).st_mode & 0o777 == 0o600

    def test_import_accepts_a_bare_list(self, engine, tmp_path):
        source = tmp_path / "bare.json"
        source.write_text(json.dumps([{"title": "Bare", "password": "a-good-password-here-7!"}]), encoding="utf-8")
        assert engine.import_plain(str(source))["added"] == 1

    def test_import_rejects_a_bad_shape(self, engine, tmp_path):
        source = tmp_path / "bad.json"
        source.write_text(json.dumps({"entries": "not-a-list"}), encoding="utf-8")
        with pytest.raises(VaultError):
            engine.import_plain(str(source))

    def test_import_regenerates_duplicate_ids(self, engine, tmp_path):
        engine.add(title="A", password="a-good-password-here-7!")
        destination = str(tmp_path / "export.json")
        engine.export_plain(destination)
        engine.import_plain(destination)
        ids = [e["id"] for e in engine.list_all()]
        assert len(ids) == len(set(ids))

    def test_backup_then_unlock_from_backup(self, engine, tmp_path):
        engine.add(title="Kept", password="a-good-password-here-7!")
        destination = str(tmp_path / "backup.json")
        engine.backup(destination)
        restored = VaultEngine(destination)
        restored.unlock(TEST_MASTER)
        assert restored.list_all()[0]["title"] == "Kept"

    def test_import_assigns_fingerprints_so_reuse_is_detected(self, engine, tmp_path):
        source = tmp_path / "twins.json"
        source.write_text(
            json.dumps(
                [
                    {"title": "Twin A", "password": "identical-password-9!"},
                    {"title": "Twin B", "password": "identical-password-9!"},
                ]
            ),
            encoding="utf-8",
        )
        engine.import_plain(str(source))
        assert len(engine.duplicates()) == 1
