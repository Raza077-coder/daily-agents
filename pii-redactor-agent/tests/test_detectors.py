"""Detector tests.

Two halves, and the second half matters more. Positive tests prove a detector
fires on valid input; **negative** tests prove it stays quiet on the lookalikes
that fill real documents (order ids, build stamps, version strings). A redaction
tool that cries wolf gets switched off, so a false positive is a real defect.
"""

from __future__ import annotations

import pytest

from veil import demo
from veil.detectors import (
    detect_all,
    detect_cards,
    detect_dob,
    detect_emails,
    detect_ibans,
    detect_ipv4,
    detect_ipv6,
    detect_macs,
    detect_national_ids,
    detect_persons,
    detect_phones,
    detect_secrets,
    detect_ssns,
    detect_urls,
    email_valid,
    iban_valid,
    ipv4_valid,
    luhn_valid,
    ssn_valid,
)


# ---------------------------------------------------------------------------
# Validators, in isolation
# ---------------------------------------------------------------------------


class TestLuhn:
    @pytest.mark.parametrize("number", [
        "4111111111111111", "5500000000000004", "4012888888881881",
        "378282246310005", "6011111111111117", "30569309025904",
    ])
    def test_accepts_valid(self, number):
        assert luhn_valid(number) is True

    @pytest.mark.parametrize("number", [
        "4111111111111112", "1234567890123456", "5500000000000005",
    ])
    def test_rejects_bad_checksum(self, number):
        assert luhn_valid(number) is False

    @pytest.mark.parametrize("value", ["", "not-digits", "4111-1111"])
    def test_rejects_non_digits(self, value):
        assert luhn_valid(value) is False


class TestIban:
    @pytest.mark.parametrize("iban", [
        "GB82 WEST 1234 5698 7654 32",
        "DE89370400440532013000",
        "FR1420041010050500013M02606",
    ])
    def test_accepts_valid(self, iban):
        assert iban_valid(iban) is True

    @pytest.mark.parametrize("iban", [
        "GB82 WEST 1234 5698 7654 33",   # bad check digits
        "XX00 0000 0000 0000",           # wrong length
        "GB82",                          # too short
    ])
    def test_rejects_invalid(self, iban):
        assert iban_valid(iban) is False


class TestSsn:
    @pytest.mark.parametrize("ssn", ["214559876", "467321234", "001234567"])
    def test_accepts_valid(self, ssn):
        assert ssn_valid(ssn) is True

    @pytest.mark.parametrize("ssn", [
        "000123456", "666123456", "900123456", "123004567", "123450000",
        "123456789", "111111111",
    ])
    def test_rejects_issuance_rules(self, ssn):
        assert ssn_valid(ssn) is False


class TestIpv4Validator:
    def test_accepts(self):
        assert ipv4_valid(("192", "168", "1", "1")) is True
        assert ipv4_valid(("0", "0", "0", "0")) is True
        assert ipv4_valid(("255", "255", "255", "255")) is True

    @pytest.mark.parametrize("octets", [
        ("256", "1", "1", "1"), ("1", "1", "1", "999"), ("01", "2", "3", "4"),
        ("1", "2", "3"),
    ])
    def test_rejects(self, octets):
        assert ipv4_valid(octets) is False


class TestEmailValidator:
    @pytest.mark.parametrize("address", [
        "a@b.co", "first.last@sub.example.com", "user+tag@example.co.uk",
    ])
    def test_accepts(self, address):
        assert email_valid(address) is True

    @pytest.mark.parametrize("address", [
        ".lead@example.com", "trail.@example.com", "two..dots@example.com",
        "no-at-sign.com", "a@b", "a@b.c", "a@-bad.com", "a@b.c0m",
    ])
    def test_rejects(self, address):
        assert email_valid(address) is False


# ---------------------------------------------------------------------------
# Positive detection
# ---------------------------------------------------------------------------


class TestPositiveDetection:
    def test_secrets_provider_formats(self):
        text = (
            "aws=AKIASYNTHETICKEY0000\n"
            "gh=ghp_SYNTHETICPLACEHOLDER000000000000000000\n"
            "slack=xoxb-SYNTHETIC-NOT-A-REAL-TOKEN\n"
            "stripe=sk_test_SYNTHETICPLACEHOLDER00\n"
        )
        found = {span.value for span in detect_secrets(text)}
        assert "AKIASYNTHETICKEY0000" in found
        assert any(v.startswith("ghp_") for v in found)
        assert any(v.startswith("xoxb-") for v in found)
        assert any(v.startswith("sk_test_") for v in found)

    def test_secret_assignment_captures_value_not_key(self):
        text = "db_password=testkey-ZephyrCove-7193"
        spans = detect_secrets(text)
        assert len(spans) == 1
        assert spans[0].value == "testkey-ZephyrCove-7193"
        assert "password" not in spans[0].value

    @pytest.mark.parametrize("prefix", ["db_", "my_", "stripe_", "aws_", ""])
    def test_prefixed_credential_keys_are_recognised(self, prefix):
        text = f"{prefix}api_key = averysecretvalue123"
        assert len(detect_secrets(text)) == 1

    def test_jwt(self):
        text = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
                "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
        assert len(detect_secrets(text)) == 1

    def test_pem_private_key(self):
        # The body is deliberately not base64: a fixture that decodes to a real
        # key would be a genuine leak, so the marker word goes inside the blocks.
        text = ("-----BEGIN RSA PRIVATE KEY-----\n"
                "SYNTHETIC-NOT-A-REAL-KEY\n"
                "-----END RSA PRIVATE KEY-----")
        spans = detect_secrets(text)
        assert len(spans) == 1
        assert spans[0].detector == "pem_private_key"

    def test_bearer_captures_only_the_token(self):
        text = "Authorization: Bearer abcdef1234567890ABCDEF"
        spans = detect_secrets(text)
        assert len(spans) == 1
        assert spans[0].value == "abcdef1234567890ABCDEF"
        assert "Bearer" not in spans[0].value

    def test_cards(self):
        text = "pay with 4111 1111 1111 1111 today"
        spans = detect_cards(text)
        assert len(spans) == 1
        assert spans[0].value == "4111 1111 1111 1111"

    def test_ssn(self):
        spans = detect_ssns("ssn: 214-55-9876")
        assert len(spans) == 1

    def test_national_id(self):
        spans = detect_national_ids("cnic 35202-1234567-1")
        assert len(spans) == 1

    def test_ipv4_and_ipv6_and_mac(self):
        assert len(detect_ipv4("host 192.168.14.22 up")) == 1
        assert len(detect_ipv6("peer 2001:0db8:85a3:0000:0000:8a2e:0370:7334")) == 1
        assert len(detect_macs("nic 00:1B:44:11:3A:B7")) == 1

    def test_url(self):
        spans = detect_urls("see https://example.com/a?b=1 for details")
        assert len(spans) == 1
        assert spans[0].value == "https://example.com/a?b=1"

    def test_url_trims_trailing_sentence_punctuation(self):
        spans = detect_urls("go to https://example.com/page.")
        assert spans[0].value == "https://example.com/page"

    def test_phone_with_country_code(self):
        assert len(detect_phones("call +92 300 1234567 now")) == 1

    def test_phone_with_parenthesised_area_code(self):
        spans = detect_phones("ring (415) 555-0132 today")
        assert len(spans) == 1
        assert spans[0].value == "(415) 555-0132"

    def test_phone_requires_context_without_separators(self):
        assert len(detect_phones("phone 4155550132")) == 1
        assert len(detect_phones("ref 4155550132")) == 0

    def test_dob_requires_birth_context(self):
        assert len(detect_dob("dob: 1988-04-12")) == 1
        assert len(detect_dob("released 1988-04-12")) == 0

    def test_dob_textual_month(self):
        assert len(detect_dob("born 12 April 1988")) == 1

    def test_person_honorific(self):
        # Two detectors can legitimately claim the same person: the honorific
        # match ("Dr. Nadia Rehman") and the dictionary match ("Nadia Rehman").
        # Detectors report both; the scanner's resolver picks the longer.
        spans = detect_persons("Please meet Dr. Nadia Rehman.")
        assert any(s.detector == "honorific" for s in spans)
        honorific = [s for s in spans if s.detector == "honorific"][0]
        assert honorific.value == "Dr. Nadia Rehman"

    def test_overlapping_person_spans_resolve_to_the_longer(self):
        from veil.scanner import resolve_spans
        spans = detect_persons("Please meet Dr. Nadia Rehman.")
        resolved = resolve_spans(spans)
        assert len(resolved) == 1
        assert resolved[0].value == "Dr. Nadia Rehman"

    def test_person_from_dictionary(self):
        assert len(detect_persons("Alice Johnson signed off.")) == 1

    def test_person_extra_names_from_policy(self):
        assert len(detect_persons("Zubair Qureshi called.", ["Zubair"])) == 1


# ---------------------------------------------------------------------------
# Negative detection — the false-positive guards
# ---------------------------------------------------------------------------


class TestNoFalsePositives:
    def test_invalid_card_is_not_reported(self):
        # Fails Luhn: a 16-digit order reference, not a card.
        assert detect_cards("order 1234567890123456 shipped") == []

    def test_ordinary_long_number_is_not_a_card(self):
        assert detect_cards("timestamp 1758170000000000") == []

    def test_bare_date_is_not_a_phone(self):
        # Eight digits and two separators is exactly the phone shape.
        assert detect_phones("released 2026-09-18 today") == []

    def test_iso_timestamp_is_not_a_phone(self):
        assert detect_phones("at 2026-09-18T04:12:07Z") == []

    def test_ip_address_is_not_a_phone(self):
        assert detect_phones("host 192.168.14.22 up") == []

    def test_version_string_is_not_a_phone(self):
        assert detect_phones("upgraded to 1.2.3") == []

    def test_placeholder_secrets_are_suppressed(self):
        text = (
            "API_KEY=changeme\n"
            "OTHER_API_KEY=your_api_key\n"
            "TOKEN=REPLACE_ME\n"
            "PASSWORD=password\n"
        )
        assert detect_secrets(text) == []

    def test_low_entropy_value_is_suppressed(self):
        assert detect_secrets("api_key=aaaaaaaaaaaa") == []

    def test_short_value_is_not_a_secret(self):
        assert detect_secrets("token=abc") == []

    def test_reserved_example_ssn_is_suppressed(self):
        assert detect_ssns("example 123-45-6789") == []

    def test_email_without_tld_is_not_reported(self):
        assert detect_emails("user@localhost") == []

    def test_person_is_off_by_default(self):
        # Opt-in: a dictionary name in prose would otherwise flood reports.
        found = detect_all("Alice Johnson signed off.", enabled=["EMAIL"])
        assert all(span.entity != "PERSON" for span in found)

    def test_plain_prose_finds_nothing(self):
        text = (
            "The quarterly review is scheduled for next Tuesday. Please bring "
            "the reconciliation notes and a summary of the open action items."
        )
        assert detect_all(text) == []


# ---------------------------------------------------------------------------
# Ordering and determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_spans_are_sorted_by_position(self):
        spans = detect_all(demo.DOCUMENTS["app.log"])
        positions = [s.start for s in spans]
        assert positions == sorted(positions)

    def test_repeated_detection_is_identical(self):
        first = detect_all(demo.DOCUMENTS["vendor_email.txt"])
        second = detect_all(demo.DOCUMENTS["vendor_email.txt"])
        assert [s.to_dict() for s in first] == [s.to_dict() for s in second]

    def test_confidences_are_bounded(self):
        for span in detect_all(demo.DOCUMENTS["vendor_email.txt"]):
            assert 0.0 < span.confidence <= 1.0

    def test_every_span_names_its_detector_and_note(self):
        for span in detect_all(demo.DOCUMENTS["vendor_email.txt"]):
            assert span.detector
            assert span.note


# ---------------------------------------------------------------------------
# Coverage of the sample bundle
# ---------------------------------------------------------------------------


class TestBundleCoverage:
    def test_email_document_exercises_every_default_entity(self):
        found = {s.entity for s in detect_all(demo.DOCUMENTS["vendor_email.txt"])}
        expected = {
            "SECRET", "CREDIT_CARD", "SSN", "IBAN", "NATIONAL_ID", "EMAIL",
            "PHONE", "DATE_OF_BIRTH", "IPV4", "IPV6", "MAC", "URL",
        }
        assert expected <= found, f"missing: {sorted(expected - found)}"

    def test_person_only_appears_when_enabled(self):
        text = demo.DOCUMENTS["vendor_email.txt"]
        assert "PERSON" not in {s.entity for s in detect_all(text)}
        assert "PERSON" in {s.entity for s in detect_all(text, enabled=["ALL"])}

    def test_all_keyword_matches_the_full_detector_list(self):
        from veil.detectors import DETECTOR_ORDER
        text = demo.DOCUMENTS["vendor_email.txt"]
        via_all = detect_all(text, enabled=["ALL"])
        via_list = detect_all(text, enabled=list(DETECTOR_ORDER))
        assert [s.to_dict() for s in via_all] == [s.to_dict() for s in via_list]

    def test_entity_names_are_case_insensitive(self):
        text = demo.DOCUMENTS["vendor_email.txt"]
        assert detect_all(text, enabled=["email"]) == detect_all(text, enabled=["EMAIL"])

    def test_config_document_yields_real_secrets_only(self):
        found = {s.value for s in detect_secrets(demo.DOCUMENTS["deployment.env"])}
        assert demo.SAMPLE_AWS_KEY in found
        assert demo.SAMPLE_GH_TOKEN in found
        assert demo.SAMPLE_API_KEY in found
        assert demo.SAMPLE_PASSWORD in found
        assert "changeme" not in found
        assert "REPLACE_ME" not in found
