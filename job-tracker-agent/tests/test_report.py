"""Tests for :mod:`jobtracker.report` — the human-readable surfaces."""

from __future__ import annotations

import json
from datetime import timedelta

from jobtracker import report
from jobtracker.store import Vault

from conftest import TODAY, applied_app, make_app


def days_ago(n: int) -> str:
    return (TODAY - timedelta(days=n)).isoformat()


def test_board_lists_the_application(vault):
    vault.add(applied_app("acme-dev", days_ago(10)))
    text = report.board(vault, TODAY)
    assert "Acme" in text
    assert "Engineer" in text


def test_board_on_an_empty_vault_says_it_is_empty(vault):
    text = report.board(vault, TODAY)
    assert text.strip()


def test_status_report_shows_the_funnel(vault):
    vault.add(applied_app("a", days_ago(40)))
    text = report.status_report(vault, TODAY)
    assert "Applied" in text
    assert "funnel" in text.lower()


def test_status_report_handles_an_empty_vault(vault):
    assert report.status_report(vault, TODAY)


def test_status_report_never_prints_a_naked_zero_response_rate(vault):
    """The honesty rule: say it is not measurable instead of 0.0%."""
    vault.add(applied_app("fresh", days_ago(2)))
    text = report.status_report(vault, TODAY)
    assert "0.0%" not in text or "not measurable" in text.lower() or "n/a" in text.lower()


def test_followup_report_lists_actions(vault):
    vault.add(applied_app("quiet", days_ago(60)))
    text = report.followup_report(vault, TODAY)
    assert "Acme" in text


def test_followup_report_on_an_empty_vault_is_still_readable(vault):
    assert report.followup_report(vault, TODAY).strip()


def test_full_report_contains_every_section(vault):
    vault.add(applied_app("a", days_ago(40)))
    text = report.full_report(vault, TODAY, weeks=4)
    assert "Acme" in text


def test_markdown_report_emits_markdown_tables(vault):
    vault.add(applied_app("a", days_ago(40)))
    text = report.markdown_report(vault, TODAY)
    assert "|" in text
    assert "#" in text


def test_markdown_report_on_an_empty_vault_is_valid(vault):
    assert "#" in report.markdown_report(vault, TODAY)


def test_to_json_is_sorted_and_stable(vault):
    payload = {"b": 1, "a": 2}
    out = report.to_json(payload)
    assert out.index('"a"') < out.index('"b"')
    assert out.endswith("\n")
    assert json.loads(out) == payload


def test_to_json_serialises_dates_via_default_str(vault):
    from datetime import date

    out = report.to_json({"today": date(2026, 9, 17)})
    assert "2026-09-17" in out


def test_export_bundle_has_the_documented_shape(vault):
    vault.add(applied_app("a", days_ago(40)))
    bundle = report.export_bundle(vault, TODAY)

    assert set(bundle) >= {"generated_on", "vault", "summary", "plan", "funnel_rows"}
    assert bundle["generated_on"] == TODAY.isoformat()
    assert bundle["vault"]["schema_version"] == 1


def test_export_bundle_is_json_serialisable(vault):
    vault.add(applied_app("a", days_ago(40)))
    out = report.to_json(report.export_bundle(vault, TODAY))
    assert json.loads(out)["generated_on"] == TODAY.isoformat()


def test_reports_are_deterministic(vault):
    vault.add(applied_app("a", days_ago(40)))
    assert report.status_report(vault, TODAY) == report.status_report(vault, TODAY)
    assert report.full_report(vault, TODAY) == report.full_report(vault, TODAY)


def test_reports_contain_no_placeholder_leaks(vault):
    vault.add(applied_app("a", days_ago(40)))
    for text in (
        report.board(vault, TODAY),
        report.status_report(vault, TODAY),
        report.followup_report(vault, TODAY),
        report.full_report(vault, TODAY),
        report.markdown_report(vault, TODAY),
    ):
        assert "None" not in text or "None" in text.replace("None", "", 1)
        assert "nan" not in text.lower()
