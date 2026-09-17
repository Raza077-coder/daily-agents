"""Tests for :mod:`jobtracker.cli` — argument parsing, exit codes, JSON mode."""

from __future__ import annotations

import json

import pytest

from jobtracker.cli import EXIT_ERROR, EXIT_OK, build_parser, main, run

TODAY = "2026-09-17"


@pytest.fixture
def vault_path(tmp_path):
    return str(tmp_path / "jobflow.json")


def cli(vault_path, *args):
    """Run the CLI against an isolated vault and return the exit code."""
    return run(["--vault", vault_path, "--today", TODAY, *args])


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------


def test_parser_has_every_documented_command():
    parser = build_parser()
    actions = [a for a in parser._actions if hasattr(a, "choices") and a.choices]
    commands = set()
    for action in actions:
        if action.dest == "command":
            commands = set(action.choices)
    for name in (
        "add", "log-apply", "move", "stage", "reopen", "drop", "interview",
        "note", "followup", "next", "edit", "list", "board", "show", "status",
        "plan", "report", "md", "export", "vocab", "init", "upcoming", "why", "demo",
    ):
        assert name in commands, f"missing command: {name}"


def test_parser_exposes_global_vault_and_today_flags():
    parser = build_parser()
    dests = {a.dest for a in parser._actions}
    assert {"vault", "today", "json"} <= dests


# --------------------------------------------------------------------------
# Happy paths
# --------------------------------------------------------------------------


def test_no_command_prints_help_and_succeeds(vault_path, capsys):
    assert main(["--vault", vault_path]) == EXIT_OK
    assert "usage" in capsys.readouterr().out.lower()


def test_init_creates_the_vault(vault_path):
    assert cli(vault_path, "init") == EXIT_OK


def test_add_then_list(vault_path, capsys):
    assert cli(vault_path, "add", "Acme", "Backend Engineer") == EXIT_OK
    capsys.readouterr()
    assert cli(vault_path, "list") == EXIT_OK
    assert "Acme" in capsys.readouterr().out


def test_add_json_mode_is_parseable(vault_path, capsys):
    assert cli(vault_path, "add", "Acme", "Dev", "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["added"]["id"] == "acme-dev"


def test_full_lifecycle_walk(vault_path):
    assert cli(vault_path, "add", "Acme", "Dev") == EXIT_OK
    assert cli(vault_path, "log-apply", "acme-dev") == EXIT_OK
    assert cli(vault_path, "move", "acme-dev", "screen") == EXIT_OK
    assert cli(vault_path, "move", "acme-dev", "interview") == EXIT_OK
    assert cli(vault_path, "note", "acme-dev", "went well") == EXIT_OK
    assert cli(vault_path, "followup", "acme-dev") == EXIT_OK
    assert cli(vault_path, "next", "acme-dev", "Send the take-home") == EXIT_OK
    assert cli(vault_path, "move", "acme-dev", "rejected") == EXIT_OK


def test_stage_backfills_history(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied")
    capsys.readouterr()
    assert cli(vault_path, "stage", "acme-dev", "onsite", "--on", "2026-09-01") == EXIT_OK


def test_interview_schedule_and_complete(vault_path):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied")
    assert cli(vault_path, "interview", "acme-dev", "--on", "2026-09-20", "--kind", "technical") == EXIT_OK
    assert cli(vault_path, "interview", "acme-dev", "--on", "2026-09-20", "--done") == EXIT_OK


def test_upcoming_lists_the_scheduled_interview(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied")
    cli(vault_path, "interview", "acme-dev", "--on", "2026-09-20", "--kind", "technical")
    capsys.readouterr()
    assert cli(vault_path, "upcoming", "--days", "14") == EXIT_OK
    assert "Acme" in capsys.readouterr().out


def test_reopen_a_rejected_application(vault_path):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied")
    cli(vault_path, "move", "acme-dev", "rejected")
    assert cli(vault_path, "reopen", "acme-dev") == EXIT_OK


def test_drop_removes_it(vault_path):
    cli(vault_path, "add", "Acme", "Dev")
    assert cli(vault_path, "drop", "acme-dev") == EXIT_OK


def test_edit_updates_a_field(vault_path):
    cli(vault_path, "add", "Acme", "Dev")
    assert cli(vault_path, "edit", "acme-dev", "--location", "Karachi, PK") == EXIT_OK


def test_every_read_command_succeeds_on_a_seeded_vault(vault_path):
    assert cli(vault_path, "demo", "--force") == EXIT_OK
    for command in ("list", "board", "status", "plan", "report", "md", "export", "vocab"):
        assert cli(vault_path, command) == EXIT_OK, f"{command} failed"


def test_show_details_one_application(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied")
    capsys.readouterr()
    assert cli(vault_path, "show", "acme-dev") == EXIT_OK
    assert "Acme" in capsys.readouterr().out


def test_why_explains_a_fired_rule(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied", "--applied-on", "2026-07-01")
    capsys.readouterr()
    assert cli(vault_path, "why", "no_response_overdue") == EXIT_OK
    assert "why:" in capsys.readouterr().out


def test_why_reports_a_rule_that_did_not_fire(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev")
    capsys.readouterr()
    assert cli(vault_path, "why", "no_response_overdue") == EXIT_OK
    assert "did not fire" in capsys.readouterr().out


def test_status_json_carries_the_summary(vault_path, capsys):
    cli(vault_path, "demo", "--force")
    capsys.readouterr()
    assert cli(vault_path, "status", "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert "totals" in payload["summary"]


def test_export_is_valid_json(vault_path, capsys):
    cli(vault_path, "demo", "--force")
    capsys.readouterr()
    assert cli(vault_path, "export") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["generated_on"] == TODAY
    assert "vault" in payload


def test_vocab_lists_the_vocabulary(vault_path, capsys):
    assert cli(vault_path, "vocab", "--json") == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert "applied" in payload["stages"]


def test_demo_refuses_to_clobber_without_force(vault_path):
    cli(vault_path, "demo", "--force")
    assert main(["--vault", vault_path, "--today", TODAY, "demo"]) == EXIT_ERROR


# --------------------------------------------------------------------------
# Exit codes — part of the contract
# --------------------------------------------------------------------------


def test_unknown_id_exits_one_with_a_clean_message(vault_path, capsys):
    assert main(["--vault", vault_path, "--today", TODAY, "show", "nope"]) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_skipping_a_rung_exits_one(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied")
    capsys.readouterr()
    assert main(["--vault", vault_path, "--today", TODAY, "move", "acme-dev", "onsite"]) == EXIT_ERROR
    assert "skips" in capsys.readouterr().err


def test_a_bad_status_choice_is_rejected_by_argparse(vault_path):
    with pytest.raises(SystemExit):
        run(["--vault", vault_path, "add", "Acme", "Dev", "--status", "ghosted"])


def test_a_malformed_today_is_reported_cleanly(vault_path, capsys):
    assert main(["--vault", vault_path, "--today", "17/09/2026", "status"]) == EXIT_ERROR
    assert "error:" in capsys.readouterr().err


def test_an_inverted_salary_range_exits_one(vault_path, capsys):
    code = main(
        [
            "--vault", vault_path, "--today", TODAY,
            "add", "Acme", "Dev",
            "--salary-min", "200000", "--salary-max", "100000",
        ]
    )
    assert code == EXIT_ERROR
    assert "salary" in capsys.readouterr().err


def test_reopening_an_open_application_exits_one(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev", "--status", "applied")
    capsys.readouterr()
    assert main(["--vault", vault_path, "--today", TODAY, "reopen", "acme-dev"]) == EXIT_ERROR


def test_followup_before_applying_exits_one(vault_path, capsys):
    cli(vault_path, "add", "Acme", "Dev")
    capsys.readouterr()
    assert main(["--vault", vault_path, "--today", TODAY, "followup", "acme-dev"]) == EXIT_ERROR
    assert "applied" in capsys.readouterr().err
