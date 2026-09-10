"""CLI tests: argument surface, exit codes, JSON output, non-interactive use."""

from __future__ import annotations

import json
import os

import pytest

from vaultguard.cli import EXIT_AUTH, EXIT_ERROR, EXIT_OK, main

from .conftest import FAST_ITERATIONS, TEST_MASTER


def run(vault_path, *args, expect=EXIT_OK, capsys=None):
    """Invoke the CLI with the master password supplied on stdin."""
    argv = ["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "--master-stdin", *args]
    return main(argv)


@pytest.fixture()
def cli_vault(vault_path):
    """A vault created through the CLI itself."""
    code = main(
        ["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init"],
        )
    return vault_path


class TestInit:
    def test_init_creates_a_vault(self, vault_path, monkeypatch):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        assert main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init"]) == EXIT_OK
        assert os.path.exists(vault_path)

    def test_init_json_output(self, vault_path, monkeypatch, capsys):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "created"

    def test_init_with_demo_seed(self, vault_path, monkeypatch, capsys):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init", "--demo", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["seeded"] is True

    def test_missing_master_returns_auth_error(self, vault_path, monkeypatch, capsys):
        """With no password on env, argv or a TTY the CLI must fail cleanly."""
        import io
        import sys

        monkeypatch.delenv("VAULTGUARD_MASTER", raising=False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(""))
        code = main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init"])
        assert code == EXIT_AUTH
        assert not os.path.exists(vault_path)


class TestEntryCommands:
    @pytest.fixture()
    def vault(self, vault_path, monkeypatch):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init"])
        return vault_path

    def test_add_and_list(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "add", "GitHub", "-p", "a-good-password-here-7!"])
        capsys.readouterr()
        assert main(["--vault", vault, "--master", TEST_MASTER, "list"]) == EXIT_OK
        assert "GitHub" in capsys.readouterr().out

    def test_add_with_generation(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "add", "Gen", "-g", "-l", "22", "--json"])
        capsys.readouterr()
        main(["--vault", vault, "--master", TEST_MASTER, "list", "--json"])
        entries = json.loads(capsys.readouterr().out)
        assert entries[0]["title"] == "Gen"

    def test_search_json(self, vault, capsys):
        for title in ("GitHub", "Gmail", "Router"):
            main(["--vault", vault, "--master", TEST_MASTER, "add", title, "-p", "a-good-password-here-7!"])
        capsys.readouterr()
        main(["--vault", vault, "--master", TEST_MASTER, "search", "git", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert [e["title"] for e in payload] == ["GitHub"]

    def test_get_masks_the_password(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "add", "A", "-p", "TopSecretValue123!", "--json"])
        entry_id = json.loads(capsys.readouterr().out)["entry"]["id"]
        main(["--vault", vault, "--master", TEST_MASTER, "get", entry_id, "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert "TopSecretValue123!" not in json.dumps(payload)

    def test_reveal_shows_the_password(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "add", "A", "-p", "TopSecretValue123!", "--json"])
        entry_id = json.loads(capsys.readouterr().out)["entry"]["id"]
        main(["--vault", vault, "--master", TEST_MASTER, "reveal", entry_id, "--json"])
        assert json.loads(capsys.readouterr().out)["password"] == "TopSecretValue123!"

    def test_update_rotates_the_password(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "add", "A", "-p", "old-password-here-7!", "--json"])
        entry_id = json.loads(capsys.readouterr().out)["entry"]["id"]
        main(["--vault", vault, "--master", TEST_MASTER, "update", entry_id, "-p", "new-password-here-8!", "--json"])
        assert json.loads(capsys.readouterr().out)["password_changed"] is True

    def test_update_without_flags_is_an_error(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "add", "A", "-p", "a-good-password-here-7!", "--json"])
        entry_id = json.loads(capsys.readouterr().out)["entry"]["id"]
        assert main(["--vault", vault, "--master", TEST_MASTER, "update", entry_id]) == EXIT_ERROR

    def test_delete_removes(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "add", "Gone", "-p", "a-good-password-here-7!", "--json"])
        entry_id = json.loads(capsys.readouterr().out)["entry"]["id"]
        assert main(["--vault", vault, "--master", TEST_MASTER, "delete", entry_id]) == EXIT_OK
        capsys.readouterr()
        main(["--vault", vault, "--master", TEST_MASTER, "list", "--json"])
        assert json.loads(capsys.readouterr().out) == []


class TestAnalysisCommands:
    @pytest.fixture()
    def vault(self, vault_path, monkeypatch):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init", "--demo"])
        return vault_path

    def test_audit_text_output(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "audit"])
        output = capsys.readouterr().out
        assert "VAULTGUARD SECURITY AUDIT" in output

    def test_audit_json_output(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "audit", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert 0 <= payload["score"] <= 100

    def test_stats(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "stats"])
        assert "Entries" in capsys.readouterr().out

    def test_suggest_without_a_vault(self, vault_path, capsys):
        assert main(["--vault", vault_path, "suggest", "-l", "20", "--json"]) == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert len(payload["password"]) == 20

    def test_generate_passphrase(self, vault, capsys):
        main(["--vault", vault, "--master", TEST_MASTER, "generate", "passphrase", "-w", "5", "--json"])
        assert len(json.loads(capsys.readouterr().out)["secret"].split("-")) >= 5

    def test_check_scores_a_password(self, capsys):
        main(["check", "qwerty123", "--json"])
        assert json.loads(capsys.readouterr().out)["score"] < 55

    def test_check_reads_from_stdin_when_omitted(self, capsys, monkeypatch):
        import io
        import sys

        monkeypatch.setattr(sys, "stdin", io.StringIO("a-strong-password-9!\n"))
        main(["check", "--json"])
        assert json.loads(capsys.readouterr().out)["grade"]

    def test_info_works_while_locked(self, vault, capsys):
        main(["--vault", vault, "info", "--json"])
        payload = json.loads(capsys.readouterr().out)
        assert payload["status"] == "locked"

    def test_demo_prints_an_audit(self, capsys):
        assert main(["demo", "--iterations", str(FAST_ITERATIONS)]) == EXIT_OK
        assert "VAULTGUARD SECURITY AUDIT" in capsys.readouterr().out


class TestMaintenanceCommands:
    @pytest.fixture()
    def vault(self, vault_path, monkeypatch):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init"])
        main(["--vault", vault_path, "--master", TEST_MASTER, "add", "A", "-p", "a-good-password-here-7!"])
        return vault_path

    def test_export_writes_a_file(self, vault, tmp_path, capsys):
        destination = str(tmp_path / "export.json")
        assert main(["--vault", vault, "--master", TEST_MASTER, "export", destination]) == EXIT_OK
        assert os.path.exists(destination)

    def test_import_adds_entries(self, vault, tmp_path, capsys):
        destination = str(tmp_path / "export.json")
        main(["--vault", vault, "--master", TEST_MASTER, "export", destination])
        capsys.readouterr()
        assert main(["--vault", vault, "--master", TEST_MASTER, "import", destination]) == EXIT_OK

    def test_backup_command(self, vault, tmp_path, capsys):
        destination = str(tmp_path / "backup.json")
        assert main(["--vault", vault, "--master", TEST_MASTER, "backup", destination]) == EXIT_OK
        assert os.path.exists(destination)

    def test_rekey_command(self, vault, capsys):
        assert main(["--vault", vault, "--master", TEST_MASTER, "rekey", "--new-master", "brand-new-secret-1"]) == EXIT_OK
        capsys.readouterr()
        assert main(["--vault", vault, "--master", "brand-new-secret-1", "list"]) == EXIT_OK

    def test_destroy_command(self, vault, capsys):
        assert main(["--vault", vault, "--master", TEST_MASTER, "destroy"]) == EXIT_OK
        assert not os.path.exists(vault)


class TestErrorHandling:
    def test_wrong_master_returns_auth_exit_code(self, vault_path, monkeypatch, capsys):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init"])
        capsys.readouterr()
        assert main(["--vault", vault_path, "--master", "wrong-password-here", "list"]) == EXIT_AUTH

    def test_missing_vault_returns_error_code(self, vault_path, capsys):
        assert main(["--vault", vault_path, "--master", TEST_MASTER, "list"]) == EXIT_ERROR

    def test_unknown_entry_returns_error_code(self, vault_path, monkeypatch, capsys):
        monkeypatch.setenv("VAULTGUARD_MASTER", TEST_MASTER)
        main(["--vault", vault_path, "--iterations", str(FAST_ITERATIONS), "init"])
        capsys.readouterr()
        assert main(["--vault", vault_path, "--master", TEST_MASTER, "get", "nope"]) == EXIT_ERROR

    def test_no_command_prints_help(self, capsys):
        assert main([]) == EXIT_OK
        assert "vaultguard" in capsys.readouterr().out.lower()
