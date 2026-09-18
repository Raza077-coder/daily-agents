"""API tests.

The API is a thin shell over the scanner, so the tests that matter are the
contract ones: status codes, validation, and the promise that the HTTP path and
the library path produce identical results for identical input.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

fastapi = pytest.importorskip("fastapi", reason="API tests need fastapi + httpx")

from fastapi.testclient import TestClient  # noqa: E402

from veil import Scanner, demo  # noqa: E402
from api.index import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


class TestHealth:
    def test_health_returns_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "veil"
        assert body["offline"] is True
        assert body["entities"] > 0


class TestEntities:
    def test_lists_every_entity(self, client):
        response = client.get("/entities")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == len(body["entities"])
        assert {"entity", "enabled", "action", "risk_weight"} <= set(body["entities"][0])

    def test_person_is_marked_opt_in(self, client):
        rows = {r["entity"]: r for r in client.get("/entities").json()["entities"]}
        assert rows["PERSON"]["opt_in"] is True
        assert rows["PERSON"]["enabled"] is False


class TestReference:
    def test_carries_the_action_table(self, client):
        body = client.get("/reference").json()
        actions = {row["action"] for row in body["actions"]}
        assert {"mask", "redact", "hash", "tokenize", "remove", "keep"} == actions

    def test_detector_order_is_exposed(self, client):
        assert len(client.get("/reference").json()["detector_order"]) == 13


class TestDemo:
    def test_returns_all_documents(self, client):
        body = client.get("/demo").json()
        assert {d["name"] for d in body["documents"]} == set(demo.DOCUMENTS)
        assert len(body["policies"]) == 3

    def test_single_document_by_name(self, client):
        response = client.get("/demo/app.log")
        assert response.status_code == 200
        assert response.json()["text"] == demo.DOCUMENTS["app.log"]

    def test_unknown_document_is_404(self, client):
        response = client.get("/demo/nope.txt")
        assert response.status_code == 404
        assert "Available" in response.json()["detail"]


class TestScan:
    def test_finds_the_expected_entities(self, client):
        response = client.post("/scan", json={"text": demo.DOCUMENTS["app.log"]})
        assert response.status_code == 200
        body = response.json()
        found = {f["entity"] for f in body["findings"]}
        assert {"EMAIL", "CREDIT_CARD", "IPV4", "SECRET"} <= found

    def test_never_returns_a_redacted_document(self, client):
        body = client.post("/scan", json={"text": demo.DOCUMENTS["app.log"]}).json()
        assert "redacted" not in body
        assert "note" in body

    def test_include_values_false_withholds_values(self, client):
        body = client.post("/scan", json={
            "text": "a@b.co", "include_values": False,
        }).json()
        assert all(f["value"] == "[withheld]" for f in body["findings"])

    def test_empty_text_is_accepted(self, client):
        response = client.post("/scan", json={"text": ""})
        assert response.status_code == 200
        assert response.json()["findings"] == []

    def test_missing_text_is_422(self, client):
        assert client.post("/scan", json={}).status_code == 422


class TestRedact:
    def test_removes_the_sensitive_values(self, client):
        response = client.post("/redact", json={"text": demo.DOCUMENTS["app.log"]})
        assert response.status_code == 200
        body = response.json()
        for secret in (demo.SAMPLE_PASSWORD, demo.SAMPLE_AWS_KEY, demo.SAMPLE_EMAIL):
            assert secret not in body["redacted"]

    def test_verification_is_clean(self, client):
        body = client.post("/redact", json={"text": demo.DOCUMENTS["app.log"]}).json()
        assert body["verification"]["status"] == "CLEAN"
        assert body["verification"]["clean"] is True

    def test_inline_policy_is_applied(self, client):
        body = client.post("/redact", json={
            "text": "keep a@b.co",
            "policy": {"entities": ["EMAIL"], "actions": {"EMAIL": "redact"}},
        }).json()
        assert "[EMAIL]" in body["redacted"]
        assert "a@b.co" not in body["redacted"]

    def test_allowlist_is_honoured(self, client):
        body = client.post("/redact", json={
            "text": "write to support@brightpath-consulting.com",
            "policy": {"allowlist": ["support@brightpath-consulting.com"]},
        }).json()
        assert "support@brightpath-consulting.com" in body["redacted"]

    def test_tokenize_returns_a_vault(self, client):
        body = client.post("/redact", json={
            "text": "a@b.co",
            "policy": {"entities": ["ALL"], "actions": {"EMAIL": "tokenize"}},
        }).json()
        assert body["token_map"] == {"VEIL_EMAIL_001": "a@b.co"}

    def test_verification_can_be_skipped(self, client):
        body = client.post("/redact", json={
            "text": "a@b.co", "verify": False,
        }).json()
        assert body["verification"] is None

    def test_invalid_policy_is_422(self, client):
        response = client.post("/redact", json={
            "text": "x", "policy": {"actions": {"EMAIL": "shred"}},
        })
        assert response.status_code == 422

    def test_unknown_entity_in_policy_is_422(self, client):
        response = client.post("/redact", json={
            "text": "x", "policy": {"entities": ["NOT_REAL"]},
        })
        assert response.status_code == 422
        # The message must name the offender and point at the catalogue: a caller
        # who typos an entity name has to be told, not silently given a 200 with
        # a document that was never actually scanned for that entity.
        detail = response.json()["detail"]
        assert "NOT_REAL" in detail
        assert "/entities" in detail

    def test_a_typoed_entity_never_silently_passes_text_through(self, client):
        # The failure this guards against is the dangerous one: `EMAIL_ADDRESS`
        # instead of `EMAIL` must not produce a cheerful 200 that leaves the
        # address in the document.
        response = client.post("/redact", json={
            "text": "reach me at alice@example.com",
            "policy": {"entities": ["EMAIL_ADDRESS"]},
        })
        assert response.status_code == 422
        assert "EMAIL_ADDRESS" in response.json()["detail"]

    def test_unknown_action_value_is_422(self, client):
        response = client.post("/redact", json={
            "text": "x", "policy": {"actions": {"EMAIL": "scramble"}},
        })
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "scramble" in detail
        assert "Valid actions are" in detail

    def test_actions_for_an_unknown_entity_is_422(self, client):
        response = client.post("/redact", json={
            "text": "x", "policy": {"actions": {"EMAILS": "mask"}},
        })
        assert response.status_code == 422
        assert "EMAILS" in response.json()["detail"]

    def test_valid_entity_names_are_still_accepted(self, client):
        response = client.post("/redact", json={
            "text": "reach me at alice@example.com",
            "policy": {"entities": ["EMAIL"], "actions": {"EMAIL": "remove"}},
        })
        assert response.status_code == 200
        assert "alice@example.com" not in response.json()["redacted"]

    def test_all_selector_is_accepted(self, client):
        # `ALL` is part of the documented selector language, so the validator
        # must let it through rather than treating it as an unknown entity.
        response = client.post("/redact", json={
            "text": "alice@example.com",
            "policy": {"entities": ["ALL"]},
        })
        assert response.status_code == 200

    def test_negated_selector_is_accepted(self, client):
        response = client.post("/redact", json={
            "text": "alice@example.com",
            "policy": {"entities": ["ALL", "!EMAIL"]},
        })
        assert response.status_code == 200

    def test_unknown_name_inside_a_negated_selector_is_422(self, client):
        response = client.post("/redact", json={
            "text": "x", "policy": {"entities": ["ALL", "!NOPE"]},
        })
        assert response.status_code == 422
        assert "NOPE" in response.json()["detail"]


class TestVerifyEndpoint:
    def test_clean_document_returns_200(self, client):
        response = client.post("/verify", json={"text": demo.DOCUMENTS["app.log"]})
        assert response.status_code == 200
        assert response.json()["verification"]["clean"] is True

    def test_reports_the_risk_level(self, client):
        body = client.post("/verify", json={"text": demo.DOCUMENTS["app.log"]}).json()
        assert body["risk_level"] in ("LOW", "MODERATE", "HIGH", "CRITICAL")


class TestDetokenize:
    def test_restores_a_value(self, client):
        body = client.post("/detokenize", json={
            "text": "mail VEIL_EMAIL_001 now",
            "vault": {"VEIL_EMAIL_001": "a@b.co"},
        }).json()
        assert body["restored"] == "mail a@b.co now"
        assert body["restored_count"] == 1

    def test_empty_vault_is_422(self, client):
        assert client.post("/detokenize", json={
            "text": "x", "vault": {},
        }).status_code == 422

    def test_entity_summary_is_returned(self, client):
        body = client.post("/detokenize", json={
            "text": "VEIL_EMAIL_001 VEIL_PHONE_001",
            "vault": {"VEIL_EMAIL_001": "a@b.co", "VEIL_PHONE_001": "+1"},
        }).json()
        assert body["by_entity"] == {"EMAIL": 1, "PHONE": 1}


class TestApiMatchesLibrary:
    """The API must not drift from the library — same input, same output."""

    def test_redacted_text_matches(self, client):
        for name, body in demo.DOCUMENTS.items():
            via_api = client.post("/redact", json={"text": body}).json()["redacted"]
            via_library = Scanner().redact(body).text
            assert via_api == via_library, f"{name} differs between API and library"

    def test_risk_score_matches(self, client):
        for name, body in demo.DOCUMENTS.items():
            via_api = client.post("/scan", json={"text": body}).json()["risk"]["score"]
            via_library = Scanner().scan(body).risk.score
            assert via_api == via_library, f"{name} risk differs"

    def test_findings_match(self, client):
        # The response schema intentionally exposes a fixed field set and omits
        # character offsets, so compare on the fields both sides publish.
        exposed = ("entity", "value", "action", "replacement", "count",
                   "detector", "confidence", "note", "preserved", "risk_weight")
        text = demo.DOCUMENTS["vendor_email.txt"]
        via_api = client.post("/scan", json={"text": text}).json()["findings"]
        via_library = [f.to_dict() for f in Scanner().scan(text).findings]
        assert len(via_api) == len(via_library)
        for api_row, lib_row in zip(via_api, via_library):
            for field in exposed:
                assert api_row[field] == lib_row[field], f"{field} differs"
            assert "offsets" not in api_row

    def test_every_shipped_policy_matches(self, client):
        from veil.policy import from_yaml
        for policy_name, yaml_body in demo.POLICIES.items():
            policy = from_yaml(yaml_body)
            for doc_name, doc in demo.DOCUMENTS.items():
                via_api = client.post("/redact", json={
                    "text": doc,
                    "policy": {k: v for k, v in policy.to_dict().items()
                               if k != "source" and v is not None},
                }).json()["redacted"]
                via_library = Scanner(policy).redact(doc).text
                assert via_api == via_library, f"{policy_name}/{doc_name} differs"
