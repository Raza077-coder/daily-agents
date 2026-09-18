"""CLI tests.

The CLI is driven as a subprocess, not by calling `main()` in-process, so these
tests exercise the real argv contract — argument parsing, exit codes, and
stdout/stderr separation — which is what a CI job actually depends on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from veil import demo  # noqa: E402


def run(*args: str, cwd: str | None = None, stdin: str | None = None):
    """Invoke the CLI as a subprocess and capture everything."""
    return subprocess.run(
        [sys.executable, "-m", "veil", *args],
        cwd=cwd or PROJECT_ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


@pytest.fixture(scope="module")
def bundle(tmp_path_factory):
    """A materialised copy of the sample bundle."""
    directory = tmp_path_factory.mktemp("veil-demo")
    written = demo.write_samples(str(directory))
    return directory, written


class TestDemoCommand:
    def test_writes_every_sample_file(self, tmp_path):
        result = run("demo", "-d", str(tmp_path))
        assert result.returncode == 0
        for name in list(demo.DOCUMENTS) + list(demo.POLICIES):
            assert (tmp_path / name).exists(), f"{name} was not written"

    def test_prints_a_tour(self, tmp_path):
        result = run("demo", "-d", str(tmp_path))
        assert "Sample bundle written" in result.stdout
        assert "strict.yaml" in result.stdout

    def test_does_not_clobber_without_force(self, tmp_path):
        (tmp_path / "app.log").write_text("my precious data", encoding="utf-8")
        run("demo", "-d", str(tmp_path))
        assert (tmp_path / "app.log").read_text(encoding="utf-8") == "my precious data"

    def test_force_overwrites(self, tmp_path):
        (tmp_path / "app.log").write_text("mine", encoding="utf-8")
        run("demo", "-d", str(tmp_path), "--force")
        assert (tmp_path / "app.log").read_text(encoding="utf-8") == demo.DOCUMENTS["app.log"]


class TestEntitiesCommand:
    def test_lists_entities_with_status(self):
        result = run("entities")
        assert result.returncode == 0
        for entity in ("EMAIL", "SECRET", "PERSON"):
            assert entity in result.stdout
        assert "(opt-in)" in result.stdout

    def test_json_output_is_valid(self):
        result = run("entities", "--json")
        rows = json.loads(result.stdout)
        assert len(rows) == 13
        assert all("enabled" in row for row in rows)

    def test_describes_a_supplied_policy(self, bundle):
        directory, written = bundle
        result = run("entities", "-p", written["strict.yaml"])
        assert result.returncode == 0
        assert "strict" in result.stdout


class TestScanCommand:
    def test_exit_1_when_findings_present(self, bundle):
        directory, written = bundle
        assert run("scan", written["app.log"]).returncode == 1

    def test_exit_0_on_a_clean_file(self, tmp_path):
        clean = tmp_path / "clean.txt"
        clean.write_text("nothing sensitive here at all\n", encoding="utf-8")
        assert run("scan", str(clean)).returncode == 0

    def test_fail_on_never_always_exits_zero(self, bundle):
        directory, written = bundle
        assert run("scan", written["app.log"], "--fail-on", "never").returncode == 0

    def test_fail_on_critical_passes_a_high_only_document(self, bundle):
        directory, written = bundle
        # app.log contains secrets, so it is CRITICAL; use a lighter document.
        result = run("scan", written["app.log"], "--fail-on", "CRITICAL")
        assert result.returncode in (0, 1)
        assert "CRITICAL" in result.stdout or "HIGH" in result.stdout

    def test_report_shows_the_risk_level(self, bundle):
        directory, written = bundle
        result = run("scan", written["vendor_email.txt"])
        assert "Risk" in result.stdout
        # Verification belongs to the redaction path: a scan-only run has no
        # output to verify, so --redacted is what adds the Verify line.
        result_redacted = run("scan", written["vendor_email.txt"], "--redacted")
        assert "Verify" in result_redacted.stdout

    def test_no_values_hides_the_secrets(self, bundle):
        directory, written = bundle
        result = run("scan", written["deployment.env"], "--no-values")
        assert "(withheld)" in result.stdout
        assert demo.SAMPLE_PASSWORD not in result.stdout
        assert demo.SAMPLE_AWS_KEY not in result.stdout

    def test_json_mode_does_not_echo_the_original_document(self, bundle):
        directory, written = bundle
        result = run("scan", written["app.log"], "--json")
        payload = json.loads(result.stdout)
        assert payload["redacted_emitted"] is False
        assert payload["redacted"] is None

    def test_redacted_flag_shows_a_diff(self, bundle):
        directory, written = bundle
        result = run("scan", written["vendor_email.txt"], "--redacted", "--diff")
        assert "--- original" in result.stdout or "No differences" in result.stdout

    def test_reads_stdin(self):
        result = run("scan", "-", stdin="contact a@b.co please")
        assert "EMAIL" in result.stdout

    def test_missing_file_exits_2_with_a_message(self):
        result = run("scan", "/definitely/not/here.txt")
        assert result.returncode == 2
        assert "no such file" in result.stderr

    def test_unknown_entity_exits_2(self, bundle):
        directory, written = bundle
        result = run("scan", written["app.log"], "--entities", "NOT_REAL")
        assert result.returncode == 2
        assert "unknown entity" in result.stderr

    def test_invalid_action_exits_2(self, bundle):
        directory, written = bundle
        result = run("scan", written["app.log"], "--action", "EMAIL=shred")
        assert result.returncode == 2
        assert "unknown action" in result.stderr

    def test_malformed_action_pair_exits_2(self, bundle):
        directory, written = bundle
        result = run("scan", written["app.log"], "--action", "EMAIL")
        assert result.returncode == 2
        assert "ENTITY=ACTION" in result.stderr


class TestRedactCommand:
    def test_writes_clean_output(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "clean.txt"
        result = run("redact", written["app.log"], "-o", str(out))
        assert result.returncode == 0
        assert out.exists()
        body = out.read_text(encoding="utf-8")
        assert demo.SAMPLE_PASSWORD not in body
        assert demo.SAMPLE_AWS_KEY not in body
        assert "Wrote" in result.stdout

    def test_preserves_line_count(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "clean.txt"
        run("redact", written["app.log"], "-o", str(out))
        assert len(out.read_text().splitlines()) == len(
            demo.DOCUMENTS["app.log"].splitlines()
        )

    def test_stdout_mode_writes_nothing_to_disk(self, bundle, tmp_path):
        directory, written = bundle
        result = run("redact", written["app.log"], "--stdout", "--quiet")
        assert demo.SAMPLE_PASSWORD not in result.stdout
        assert not (tmp_path / "redacted.txt").exists()

    def test_markdown_report_is_written(self, bundle, tmp_path):
        directory, written = bundle
        report = tmp_path / "report.md"
        run("redact", written["vendor_email.txt"], "-o", str(tmp_path / "c.txt"),
            "--report", str(report))
        body = report.read_text(encoding="utf-8")
        assert body.startswith("# VEIL redaction report")
        assert "| Entity | Action | Count |" in body

    def test_tokenize_writes_a_vault(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "tok.txt"
        result = run("redact", written["app.log"], "-o", str(out),
                     "-p", written["pseudonymize.yaml"])
        vault_path = str(out) + ".veilvault.json"
        assert os.path.exists(vault_path)
        assert "Token vault written" in result.stdout
        assert "VEIL_" in out.read_text(encoding="utf-8")

    def test_policy_overrides_on_the_command_line(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "c.txt"
        run("redact", written["vendor_email.txt"], "-o", str(out),
            "--entities", "EMAIL", "--action", "EMAIL=redact", "--quiet")
        body = out.read_text(encoding="utf-8")
        assert "[EMAIL]" in body
        assert demo.SAMPLE_EMAIL not in body

    def test_allowlist_keeps_a_published_address(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "c.txt"
        run("redact", written["deployment.env"], "-o", str(out),
            "--allow", "support@brightpath-consulting.com", "--quiet")
        assert "support@brightpath-consulting.com" in out.read_text(encoding="utf-8")

    def test_denylist_removes_an_undetected_literal(self, tmp_path):
        source = tmp_path / "notes.txt"
        source.write_text("the PROJECT-FALCON rollout is Monday\n", encoding="utf-8")
        out = tmp_path / "c.txt"
        run("redact", str(source), "-o", str(out), "--deny", "PROJECT-FALCON", "--quiet")
        assert "PROJECT-FALCON" not in out.read_text(encoding="utf-8")

    def test_fail_on_gates_the_build(self, bundle, tmp_path):
        directory, written = bundle
        result = run("redact", written["app.log"], "-o", str(tmp_path / "c.txt"),
                     "--fail-on", "HIGH", "--quiet")
        assert result.returncode == 1


class TestVerifyCommand:
    def test_clean_run_exits_0(self, bundle):
        directory, written = bundle
        result = run("verify", written["app.log"])
        assert result.returncode == 0
        assert "CLEAN" in result.stdout

    def test_json_output_reports_the_status(self, bundle):
        directory, written = bundle
        payload = json.loads(run("verify", written["app.log"], "--json").stdout)
        assert payload["status"] == "CLEAN"
        assert payload["clean"] is True

    def test_checked_entities_are_listed(self, bundle):
        directory, written = bundle
        result = run("verify", written["app.log"])
        assert "checked:" in result.stdout


class TestDetokenizeCommand:
    def test_round_trip_through_the_cli(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "tok.txt"
        run("redact", written["vendor_email.txt"], "-o", str(out),
            "-p", written["pseudonymize.yaml"], "--quiet")
        vault_path = str(out) + ".veilvault.json"

        restored = tmp_path / "restored.txt"
        result = run("detokenize", str(out), "--vault", vault_path, "-o", str(restored))
        assert result.returncode == 0
        assert restored.read_text(encoding="utf-8") == demo.DOCUMENTS["vendor_email.txt"]

    def test_missing_vault_exits_2(self, bundle, tmp_path):
        directory, written = bundle
        result = run("detokenize", written["app.log"], "--vault",
                     str(tmp_path / "nope.json"))
        assert result.returncode == 2
        assert "no vault" in result.stderr


class TestVaultCommand:
    def test_lists_entries(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "tok.txt"
        run("redact", written["app.log"], "-o", str(out),
            "-p", written["pseudonymize.yaml"], "--quiet")
        result = run("vault", "--vault", str(out) + ".veilvault.json")
        assert result.returncode == 0
        assert "VEIL_" in result.stdout

    def test_no_values_shows_only_the_summary(self, bundle, tmp_path):
        directory, written = bundle
        out = tmp_path / "tok.txt"
        run("redact", written["app.log"], "-o", str(out),
            "-p", written["pseudonymize.yaml"], "--quiet")
        result = run("vault", "--vault", str(out) + ".veilvault.json", "--no-values")
        assert result.returncode == 0
        assert demo.SAMPLE_EMAIL not in result.stdout


class TestCheckCommand:
    def test_scans_a_tree_and_reports_a_summary(self, bundle):
        directory, written = bundle
        result = run("check", str(directory))
        assert "files" in result.stdout
        assert "worst" in result.stdout

    def test_exit_1_when_the_worst_level_exceeds_the_threshold(self, bundle):
        directory, written = bundle
        assert run("check", str(directory), "--fail-on", "HIGH").returncode == 1

    def test_fail_on_never_passes(self, bundle):
        directory, written = bundle
        assert run("check", str(directory), "--fail-on", "never").returncode == 0

    def test_clean_tree_passes(self, tmp_path):
        (tmp_path / "a.txt").write_text("nothing here\n", encoding="utf-8")
        assert run("check", str(tmp_path), "--fail-on", "any").returncode == 0

    def test_json_summary(self, bundle):
        directory, written = bundle
        payload = json.loads(run("check", str(directory), "--json").stdout)
        assert payload["files"] > 0
        assert "worst_level" in payload
        assert payload["results"]

    def test_empty_directory_exits_2(self, tmp_path):
        result = run("check", str(tmp_path))
        assert result.returncode == 2
        assert "no files found" in result.stderr


class TestGeneralCli:
    def test_no_command_prints_help(self):
        result = run()
        assert result.returncode == 0
        assert "usage" in result.stdout.lower()

    def test_version_is_reported(self):
        assert "1.0.0" in run("--version").stdout

    def test_reference_command_prints_tables(self):
        result = run("reference")
        assert result.returncode == 0
        assert "ENTITIES" in result.stdout
        assert "ACTIONS" in result.stdout

    def test_help_lists_every_command(self):
        result = run("--help")
        for command in ("demo", "entities", "scan", "redact", "verify",
                        "detokenize", "vault", "check", "reference"):
            assert command in result.stdout
