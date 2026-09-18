"""Report rendering tests.

Reports are what a human actually reads before trusting the tool, so the
guarantees worth testing are: the JSON never leaks a value it was told to
withhold, the Markdown is well-formed, and the `--no-values` switch really does
remove values rather than merely hiding them.
"""

from __future__ import annotations

import json

import pytest

from veil import Scanner, demo
from veil.reports import (
    render_diff,
    render_json,
    render_markdown,
    render_reference,
    render_terminal,
)


@pytest.fixture
def result():
    return Scanner().redact(demo.DOCUMENTS["vendor_email.txt"])


@pytest.fixture
def scan_only():
    return Scanner().scan(demo.DOCUMENTS["vendor_email.txt"])


class TestTerminalReport:
    def test_contains_the_headline_facts(self, result):
        text = render_terminal(result)
        assert "VEIL" in text
        assert "Risk" in text
        assert "Policy" in text
        assert result.risk.level in text

    def test_lists_every_finding(self, result):
        text = render_terminal(result)
        for finding in result.findings:
            assert finding.entity in text

    def test_no_values_switch_removes_them(self, result):
        text = render_terminal(result, show_values=False)
        assert "(withheld)" in text
        for secret in (demo.SAMPLE_AWS_KEY, demo.SAMPLE_PASSWORD, demo.SAMPLE_EMAIL):
            assert secret not in text

    def test_offsets_are_optional(self, result):
        # The offsets row is distinguishable by its field layout, not by a bare
        # "@" (which also appears inside every email address).
        without = render_terminal(result, show_offsets=False)
        with_offsets = render_terminal(result, show_offsets=True)
        assert len(with_offsets) > len(without)
        assert "0-" in with_offsets or "," in with_offsets

    def test_clean_document_says_so(self):
        text = render_terminal(Scanner().scan("nothing here"))
        assert "Nothing detected" in text

    def test_verification_line_is_present(self, result):
        assert "Verify" in render_terminal(result)
        assert "CLEAN" in render_terminal(result)


class TestMarkdownReport:
    def test_has_a_heading_and_tables(self, result):
        text = render_markdown(result)
        assert text.startswith("# VEIL redaction report")
        assert "| Entity | Action | Count | Value | Replacement |" in text

    def test_pipes_in_values_are_escaped(self):
        result = Scanner().redact("weird|value@example.com")
        text = render_markdown(result)
        row = [l for l in text.splitlines() if "weird" in l]
        for line in row:
            assert "\\|" in line or "WITHHELD" in line.upper()

    def test_no_values_switch_removes_them(self, result):
        text = render_markdown(result, show_values=False)
        assert "_(withheld)_" in text
        assert demo.SAMPLE_PASSWORD not in text

    def test_clean_document_says_so(self):
        assert "Nothing detected" in render_markdown(Scanner().scan("clean text"))

    def test_by_entity_section_present(self, result):
        assert "Occurrences by entity" in render_markdown(result)

    def test_residuals_section_appears_only_when_needed(self, result):
        assert "Residuals" not in render_markdown(result)

    def test_kept_values_marked_as_kept(self):
        from veil.policy import from_yaml
        policy = from_yaml(demo.POLICIES["shareable.yaml"])
        result = Scanner(policy).redact(demo.DOCUMENTS["deployment.env"])
        assert "_(kept)_" in render_markdown(result)


class TestJsonReport:
    def test_is_valid_json(self, result):
        assert isinstance(json.loads(render_json(result)), dict)

    def test_redaction_result_includes_the_redacted_text(self, result):
        data = json.loads(render_json(result))
        assert data["redacted_emitted"] is True
        assert data["redacted"] is not None

    def test_scan_only_result_does_not_echo_the_original(self, scan_only):
        # The original text is still in result.text — it must not be published
        # under a key called "redacted". Findings legitimately carry individual
        # values when include_values=True, but the whole document must not
        # reappear as a blob a caller could mistake for clean output.
        data = json.loads(render_json(scan_only))
        assert data["redacted_emitted"] is False
        assert data["redacted"] is None
        assert demo.DOCUMENTS["vendor_email.txt"] not in json.dumps(data)

    def test_no_values_switch_withholds_findings(self, result):
        blob = render_json(result, include_values=False)
        data = json.loads(blob)
        assert all(f["value"] == "[withheld]" for f in data["findings"])

    def test_values_present_by_default(self, result):
        data = json.loads(render_json(result))
        assert any(f["value"] != "[withheld]" for f in data["findings"])

    def test_risk_and_verification_are_included(self, result):
        data = json.loads(render_json(result))
        assert data["risk"]["level"]
        assert data["verification"]["status"] == "CLEAN"

    def test_indent_can_be_disabled(self, result):
        assert "\n" not in render_json(result, indent=None)

    def test_unicode_is_not_escaped(self):
        # The mask character is U+2022; ensure_ascii would turn it into \u2022.
        blob = render_json(Scanner().redact("a@b.co"))
        assert "\u2022" in blob
        assert "\\u2022" not in blob


class TestDiff:
    def test_reports_no_change_when_identical(self):
        assert "No differences" in render_diff("same", "same")

    def test_shows_changed_lines(self):
        text = render_diff("a@b.co\nkeep\n", "\u2022\u2022\u2022\u2022.co\nkeep\n")
        assert "-a@b.co" in text
        assert "+" in text

    def test_truncates_very_large_diffs(self):
        before = "\n".join(f"a@b.co line {i}" for i in range(400))
        after = "\n".join(f"clean line {i}" for i in range(400))
        text = render_diff(before, after, max_lines=20)
        assert "more line(s)" in text


class TestReference:
    def test_lists_every_entity_and_action(self):
        text = render_reference()
        from veil.models import ALL_ENTITIES
        from veil.actions import describe_actions
        for entity in ALL_ENTITIES:
            assert entity in text
        for row in describe_actions():
            assert row["action"] in text

    def test_marks_opt_in_entities(self):
        assert "(opt-in)" in render_reference()
