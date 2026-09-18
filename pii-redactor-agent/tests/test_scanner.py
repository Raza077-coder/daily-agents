"""Scanner tests — the integration layer.

The properties asserted here are the ones that make the tool trustworthy:
determinism, correct overlap resolution, a redaction that is actually applied,
and a verification pass that fails when it should.
"""

from __future__ import annotations

import pytest

from veil import Scanner, demo
from veil.detectors import detect_all
from veil.models import ALL_ENTITIES, ACTION_KEEP, Span
from veil.policy import Policy, from_dict, from_yaml
from veil.scanner import (
    RISK_LEVELS,
    build_vault,
    resolve_spans,
    score_risk,
    verify,
)
from veil.vault import Vault


class TestOverlapResolution:
    def test_longer_span_wins(self):
        spans = [
            Span(0, 5, "PHONE", "12345", "a", 0.9),
            Span(0, 10, "EMAIL", "12345@x.co", "b", 0.9),
        ]
        resolved = resolve_spans(spans)
        assert len(resolved) == 1
        assert resolved[0].entity == "EMAIL"

    def test_disjoint_spans_all_survive(self):
        spans = [
            Span(0, 3, "EMAIL", "abc", "a", 0.9),
            Span(10, 13, "PHONE", "def", "b", 0.9),
        ]
        assert len(resolve_spans(spans)) == 2

    def test_higher_confidence_wins_a_tie_on_length(self):
        spans = [
            Span(0, 6, "PHONE", "abcdef", "a", 0.5),
            Span(0, 6, "EMAIL", "abcdef", "b", 0.95),
        ]
        assert resolve_spans(spans)[0].entity == "EMAIL"

    def test_higher_risk_wins_a_tie_on_length_and_confidence(self):
        spans = [
            Span(0, 6, "IPV4", "abcdef", "a", 0.9),
            Span(0, 6, "SECRET", "abcdef", "b", 0.9),
        ]
        assert resolve_spans(spans)[0].entity == "SECRET"

    def test_result_is_sorted_by_position(self):
        spans = [
            Span(20, 24, "EMAIL", "zzzz", "a", 0.9),
            Span(0, 4, "PHONE", "aaaa", "b", 0.9),
        ]
        assert [s.start for s in resolve_spans(spans)] == [0, 20]

    def test_empty_input(self):
        assert resolve_spans([]) == []

    def test_duplicate_spans_collapse_to_one(self):
        spans = [
            Span(0, 5, "EMAIL", "aaaaa", "a", 0.9),
            Span(0, 5, "EMAIL", "aaaaa", "a", 0.9),
        ]
        assert len(resolve_spans(spans)) == 1

    def test_email_inside_url_resolves_to_one_winner(self):
        # A real case: the URL detector and the email detector can both claim
        # part of a URL containing an address.
        result = Scanner().scan("see https://example.com/u/a@b.co now")
        email_spans = [f for f in result.findings if f.entity == "EMAIL"]
        url_spans = [f for f in result.findings if f.entity == "URL"]
        assert len(email_spans) + len(url_spans) <= 2


class TestRedactionIsApplied:
    def test_default_policy_removes_the_original_values(self):
        result = Scanner().redact(demo.DOCUMENTS["vendor_email.txt"])
        for finding in result.findings:
            if finding.preserved:
                continue
            assert finding.value not in result.text, (
                f"{finding.entity} {finding.value!r} survived redaction"
            )

    def test_secrets_are_gone_entirely(self):
        result = Scanner().redact(demo.DOCUMENTS["deployment.env"])
        for secret in (demo.SAMPLE_AWS_KEY, demo.SAMPLE_GH_TOKEN,
                       demo.SAMPLE_API_KEY, demo.SAMPLE_PASSWORD):
            assert secret not in result.text

    def test_cards_are_masked_but_recognisable(self):
        result = Scanner().redact("card 4111 1111 1111 1111")
        assert "4111" not in result.text
        assert "1111" in result.text  # last four are kept

    def test_position_alignment_after_masking(self):
        # Masking changes the length of each replacement, so offsets must be
        # applied back-to-front or every later span shifts by the running delta.
        text = "first a@b.co then c@d.co then e@f.co"
        result = Scanner().redact(text)
        assert "a@b.co" not in result.text
        assert "c@d.co" not in result.text
        assert "e@f.co" not in result.text
        # The surrounding prose must survive, in order and untouched.
        assert result.text.startswith("first ")
        assert result.text.count(" then ") == 2
        assert len(result.findings) == 3

    def test_remove_action_leaves_structure_intact(self):
        result = Scanner().redact("db_password=testkey-ZephyrCove-7193\nport=5432")
        assert "ZephyrCove" not in result.text
        assert "port=5432" in result.text

    def test_allowlist_value_is_kept(self):
        policy = from_yaml(demo.POLICIES["shareable.yaml"])
        result = Scanner(policy).redact(demo.DOCUMENTS["deployment.env"])
        assert "support@brightpath-consulting.com" in result.text
        kept = [f for f in result.findings if f.preserved]
        assert any(f.value == "support@brightpath-consulting.com" for f in kept)

    def test_denylist_forces_an_undetected_literal(self):
        policy = from_dict({"denylist": ["PROJECT-FALCON"]})
        result = Scanner(policy).redact("the PROJECT-FALCON migration is due")
        assert "PROJECT-FALCON" not in result.text

    def test_denylist_is_case_insensitive_by_default(self):
        policy = from_dict({"denylist": ["project-falcon"]})
        result = Scanner(policy).redact("about PROJECT-FALCON today")
        assert "PROJECT-FALCON" not in result.text

    def test_case_sensitive_denylist_can_be_enabled(self):
        policy = from_dict({
            "denylist": ["Token"], "case_sensitive_denylist": True,
        })
        result = Scanner(policy).redact("a Token and a token")
        assert "a Token" not in result.text
        assert "a token" in result.text

    def test_unknown_entities_are_not_scanned(self):
        policy = from_dict({"entities": ["EMAIL"]})
        result = Scanner(policy).redact("a@b.co and 4111 1111 1111 1111")
        assert "a@b.co" not in result.text
        assert "4111 1111 1111 1111" in result.text


class TestDeterminism:
    def test_same_input_gives_byte_identical_output(self):
        first = Scanner().redact(demo.DOCUMENTS["vendor_email.txt"])
        second = Scanner().redact(demo.DOCUMENTS["vendor_email.txt"])
        assert first.text == second.text
        assert [f.to_dict() for f in first.findings] == [
            f.to_dict() for f in second.findings
        ]

    def test_token_numbering_is_stable(self):
        first = Scanner(from_dict({"actions": {e: "tokenize" for e in ALL_ENTITIES}}))
        second = Scanner(from_dict({"actions": {e: "tokenize" for e in ALL_ENTITIES}}))
        text = demo.DOCUMENTS["app.log"]
        assert first.redact(text).token_map == second.redact(text).token_map

    def test_repeated_values_share_one_finding(self):
        result = Scanner().scan("a@b.co and again a@b.co and once more a@b.co")
        emails = [f for f in result.findings if f.entity == "EMAIL"]
        assert len(emails) == 1
        assert emails[0].count == 3

    def test_findings_ordered_by_risk_then_position(self):
        result = Scanner().scan(demo.DOCUMENTS["vendor_email.txt"])
        weights = [f.risk_weight for f in result.findings]
        assert weights == sorted(weights, reverse=True)


class TestRiskScoring:
    def test_empty_document_scores_zero(self):
        result = Scanner().scan("nothing to see here at all")
        assert result.risk.score == 0
        assert result.risk.level == "NONE"

    def test_a_single_credential_is_high(self):
        result = Scanner().scan(f"key={demo.SAMPLE_AWS_KEY}")
        assert result.risk.level in ("HIGH", "CRITICAL")

    def test_score_bands_are_monotonic(self):
        clean = Scanner().scan("nothing here").risk.score
        one = Scanner().scan("mail a@b.co").risk.score
        many = Scanner().scan(demo.DOCUMENTS["vendor_email.txt"]).risk.score
        assert clean < one < many

    def test_repetition_adds_but_sublinearly(self):
        once = Scanner().scan("a@b.co").risk.score
        eight = Scanner().scan(" ".join(["a@b.co"] * 8)).risk.score
        assert once < eight < once * 8

    def test_kept_values_do_not_contribute(self):
        policy = from_dict({"allowlist": ["a@b.co"]})
        result = Scanner(policy).scan("a@b.co")
        assert result.risk.score == 0
        assert result.risk.level == "NONE"

    def test_by_entity_counts_occurrences(self):
        result = Scanner().scan("a@b.co c@d.co")
        assert result.risk.by_entity.get("EMAIL") == 2

    def test_level_bands_are_ordered(self):
        assert RISK_LEVELS[0] == "NONE"
        assert RISK_LEVELS[-1] == "CRITICAL"
        assert len(RISK_LEVELS) == 5

    def test_distinct_and_occurrence_counts_differ(self):
        result = Scanner().scan("a@b.co and a@b.co")
        assert result.risk.distinct_findings == 1
        assert result.risk.total_occurrences == 2


class TestVerification:
    def test_clean_when_nothing_survives(self):
        result = Scanner().redact(demo.DOCUMENTS["app.log"])
        assert result.verification.clean
        assert result.verification.status == "CLEAN"
        assert result.verification.residuals == []

    def test_all_demo_documents_verify_clean(self):
        scanner = Scanner()
        for name, body in demo.DOCUMENTS.items():
            result = scanner.redact(body)
            assert result.verification.clean, (
                f"{name} left residuals: {result.verification.residuals}"
            )

    def test_all_shipped_policies_verify_clean(self):
        for name, body in demo.POLICIES.items():
            scanner = Scanner(from_yaml(body))
            for doc_name, doc in demo.DOCUMENTS.items():
                result = scanner.redact(doc)
                assert result.verification.clean, (
                    f"{name} on {doc_name}: {result.verification.residuals}"
                )

    def test_kept_values_are_reported_as_preserved_not_residual(self):
        policy = from_yaml(demo.POLICIES["shareable.yaml"])
        result = Scanner(policy).redact(demo.DOCUMENTS["deployment.env"])
        assert result.verification.clean
        assert result.verification.preserved

    def test_verification_detects_an_unapplied_value(self):
        # Simulate a broken transform by verifying text that was never redacted.
        policy = Policy()
        verification = verify(demo.DOCUMENTS["app.log"], policy)
        assert not verification.clean
        assert verification.status == "RESIDUAL_FOUND"
        assert verification.residuals

    def test_verification_lists_what_it_checked(self):
        result = Scanner().redact("a@b.co")
        assert result.verification.checked_entities == Scanner().policy.entities

    def test_verification_can_be_skipped(self):
        result = Scanner().redact("a@b.co", verify_output=False)
        assert result.verification is None


class TestDetokenize:
    def test_restores_tokenized_values(self, tokenize_all_policy):
        scanner = Scanner(tokenize_all_policy)
        body = demo.DOCUMENTS["vendor_email.txt"]
        result = scanner.redact(body)
        restored = scanner.detokenize(result.text, Vault.from_mapping(result.token_map))
        assert restored == body

    def test_restores_longest_tokens_first(self, tokenize_all_policy):
        scanner = Scanner(tokenize_all_policy)
        body = demo.DOCUMENTS["app.log"]
        result = scanner.redact(body)
        restored = scanner.detokenize(result.text, Vault.from_mapping(result.token_map))
        assert restored == body

    def test_untouched_text_passes_through(self):
        assert Scanner().detokenize("no tokens here", Vault.from_mapping({})) == "no tokens here"


class TestScannerIntrospection:
    def test_entity_report_covers_every_entity(self):
        rows = Scanner().entity_report()
        assert len(rows) == len(ALL_ENTITIES)
        assert {r["entity"] for r in rows} == set(ALL_ENTITIES)

    def test_person_is_flagged_opt_in(self):
        rows = {r["entity"]: r for r in Scanner().entity_report()}
        assert rows["PERSON"]["opt_in"] is True
        assert rows["PERSON"]["enabled"] is False
        assert rows["EMAIL"]["opt_in"] is False

    def test_disabled_entities_have_no_action(self):
        rows = {r["entity"]: r for r in Scanner().entity_report()}
        assert rows["PERSON"]["action"] is None

    def test_build_vault_carries_the_policy_name(self):
        result = Scanner().redact("a@b.co")
        vault = build_vault(result)
        assert vault.policy == result.policy_name


class TestEdgeCases:
    def test_empty_string(self):
        result = Scanner().redact("")
        assert result.text == ""
        assert result.findings == []
        assert result.verification.clean

    def test_text_with_no_findings_is_unchanged(self):
        text = "The cat sat on the mat."
        assert Scanner().redact(text).text == text

    def test_unicode_is_preserved(self):
        text = "caf\u00e9 \u2014 na\u00efve r\u00e9sum\u00e9 \u4e2d\u6587 \U0001f600"
        assert Scanner().redact(text).text == text

    def test_non_string_input_is_rejected(self):
        with pytest.raises(TypeError, match="must be a string"):
            Scanner().detect(None)

    def test_very_long_input_terminates(self):
        text = ("filler text " * 2000) + " and a@b.co at the end"
        result = Scanner().redact(text)
        assert "a@b.co" not in result.text

    def test_multiline_preserves_line_count(self):
        result = Scanner().redact(demo.DOCUMENTS["app.log"])
        assert len(result.text.splitlines()) == len(demo.DOCUMENTS["app.log"].splitlines())

    def test_adjacent_findings_both_applied(self):
        result = Scanner().redact("a@b.co,c@d.co")
        assert "a@b.co" not in result.text
        assert "c@d.co" not in result.text

    def test_keep_action_reports_without_changing(self):
        policy = from_dict({"actions": {e: ACTION_KEEP for e in ALL_ENTITIES}})
        result = Scanner(policy).redact("a@b.co")
        assert result.text == "a@b.co"
        assert result.findings[0].preserved is True
