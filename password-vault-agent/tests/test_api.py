"""REST API tests using the FastAPI TestClient."""

from __future__ import annotations

import os

import pytest

fastapi = pytest.importorskip("fastapi", reason="FastAPI not installed")
from fastapi.testclient import TestClient  # noqa: E402

from vaultguard.api import create_app  # noqa: E402

from .conftest import FAST_ITERATIONS, TEST_MASTER  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient bound to an isolated vault path."""
    path = str(tmp_path / "api-vault.json")
    monkeypatch.setenv("VAULTGUARD_PATH", path)
    app = create_app()
    with TestClient(app) as test_client:
        test_client.vault_path = path
    yield test_client


@pytest.fixture()
def seeded_client(client):
    """A client backed by a vault that already has demo data."""
    response = client.post(
        "/vault/create",
        json={
            "master_password": TEST_MASTER,
            "vault_path": client.vault_path,
            "force": True,
            "seed_demo": True,
        },
    )
    assert response.status_code == 200
    return client


class TestMetaEndpoints:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"
        assert payload["agent"] == "VAULTGUARD"
        assert payload["network_calls"] == "none"

    def test_root_lists_endpoints(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert response.json()["docs"] == "/docs"

    def test_persona_is_served(self, client):
        response = client.get("/persona")
        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "VAULTGUARD"
        assert "system_prompt" in payload
        assert payload["security"]["kdf"] == "pbkdf2-hmac-sha256"

    def test_categories(self, client):
        response = client.get("/categories")
        assert response.status_code == 200
        assert "login" in response.json()["categories"]

    def test_openapi_schema_generates(self, client):
        assert client.get("/openapi.json").status_code == 200


class TestVaultEndpoints:
    def test_create_vault(self, client):
        response = client.post(
            "/vault/create",
            json={"master_password": TEST_MASTER, "vault_path": client.vault_path},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "created"

    def test_create_rejects_short_password(self, client):
        response = client.post(
            "/vault/create",
            json={"master_password": "short", "vault_path": client.vault_path},
        )
        assert response.status_code == 422

    def test_create_requires_force_when_exists(self, seeded_client):
        response = seeded_client.post(
            "/vault/create",
            json={"master_password": TEST_MASTER, "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 409

    def test_info_reports_locked(self, seeded_client):
        response = seeded_client.get("/vault/info", params={"vault_path": seeded_client.vault_path})
        assert response.status_code == 200
        assert response.json()["status"] == "locked"

    def test_unlock_with_correct_password(self, seeded_client):
        response = seeded_client.post(
            "/vault/unlock",
            json={"master_password": TEST_MASTER, "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 200
        assert response.json()["entries"] > 0

    def test_unlock_with_wrong_password_is_401(self, seeded_client):
        response = seeded_client.post(
            "/vault/unlock",
            json={"master_password": "definitely-wrong", "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 401

    def test_unlock_on_missing_vault_is_404(self, client):
        response = client.post(
            "/vault/unlock",
            json={"master_password": TEST_MASTER, "vault_path": os.path.join(os.path.dirname(client.vault_path), "nope.json")},
        )
        assert response.status_code == 404

    def test_audit_returns_a_score(self, seeded_client):
        response = seeded_client.post(
            "/vault/audit",
            json={"master_password": TEST_MASTER, "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 200
        payload = response.json()
        assert 0 <= payload["score"] <= 100
        assert payload["findings"]

    def test_stats_includes_health(self, seeded_client):
        response = seeded_client.post(
            "/vault/stats",
            json={"master_password": TEST_MASTER, "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["total"] >= 4
        assert "health_score" in payload
        assert payload["duplicates"]  # the demo seed reuses one password on purpose


class TestEntryEndpoints:
    def test_list_entries(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries",
            json={"master_password": TEST_MASTER, "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 200
        assert response.json()["count"] >= 4

    def test_list_filters_by_text(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries",
            json={"master_password": TEST_MASTER, "text": "github", "vault_path": seeded_client.vault_path},
        )
        titles = [e["title"] for e in response.json()["entries"]]
        assert "GitHub" in titles

    def test_entries_are_masked_in_listings(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries",
            json={"master_password": TEST_MASTER, "vault_path": seeded_client.vault_path},
        )
        assert "Xk9#mQ2vLp7$Zw4Rt8Nb" not in response.text

    def test_add_entry(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries/add",
            json={
                "master_password": TEST_MASTER,
                "title": "New Account",
                "username": "raza",
                "password": "a-good-password-here-7!",
                "vault_path": seeded_client.vault_path,
            },
        )
        assert response.status_code == 200
        assert response.json()["entry"]["title"] == "New Account"

    def test_add_with_generation(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries/add",
            json={
                "master_password": TEST_MASTER,
                "title": "Generated",
                "generate": True,
                "length": 24,
                "vault_path": seeded_client.vault_path,
            },
        )
        assert response.status_code == 200
        assert response.json()["generated"] is True

    def test_add_rejects_a_short_generated_length(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries/add",
            json={
                "master_password": TEST_MASTER,
                "title": "TooShort",
                "generate": True,
                "length": 4,
                "vault_path": seeded_client.vault_path,
            },
        )
        assert response.status_code == 400

    def test_get_masks_and_reveals(self, seeded_client):
        created = seeded_client.post(
            "/vault/entries/add",
            json={
                "master_password": TEST_MASTER,
                "title": "Reveal Me",
                "password": "TopSecretValue123!",
                "vault_path": seeded_client.vault_path,
            },
        ).json()
        entry_id = created["entry"]["id"]

        masked = seeded_client.post(
            "/vault/entries/get",
            json={"master_password": TEST_MASTER, "entry_id": entry_id, "vault_path": seeded_client.vault_path},
        ).json()
        assert "TopSecretValue123!" not in str(masked)

        revealed = seeded_client.post(
            "/vault/entries/get",
            json={
                "master_password": TEST_MASTER,
                "entry_id": entry_id,
                "reveal": True,
                "vault_path": seeded_client.vault_path,
            },
        ).json()
        assert revealed["password"] == "TopSecretValue123!"

    def test_get_unknown_entry_is_404(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries/get",
            json={"master_password": TEST_MASTER, "entry_id": "nope", "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 404

    def test_update_entry(self, seeded_client):
        created = seeded_client.post(
            "/vault/entries/add",
            json={
                "master_password": TEST_MASTER,
                "title": "Before",
                "password": "a-good-password-here-7!",
                "vault_path": seeded_client.vault_path,
            },
        ).json()
        response = seeded_client.post(
            "/vault/entries/update",
            json={
                "master_password": TEST_MASTER,
                "entry_id": created["entry"]["id"],
                "title": "After",
                "vault_path": seeded_client.vault_path,
            },
        )
        assert response.status_code == 200
        assert response.json()["entry"]["title"] == "After"

    def test_update_with_no_fields_is_400(self, seeded_client):
        created = seeded_client.post(
            "/vault/entries/add",
            json={
                "master_password": TEST_MASTER,
                "title": "NoChange",
                "password": "a-good-password-here-7!",
                "vault_path": seeded_client.vault_path,
            },
        ).json()
        response = seeded_client.post(
            "/vault/entries/update",
            json={
                "master_password": TEST_MASTER,
                "entry_id": created["entry"]["id"],
                "vault_path": seeded_client.vault_path,
            },
        )
        assert response.status_code == 400

    def test_delete_entry(self, seeded_client):
        created = seeded_client.post(
            "/vault/entries/add",
            json={
                "master_password": TEST_MASTER,
                "title": "Doomed",
                "password": "a-good-password-here-7!",
                "vault_path": seeded_client.vault_path,
            },
        ).json()
        response = seeded_client.post(
            "/vault/entries/delete",
            json={
                "master_password": TEST_MASTER,
                "entry_id": created["entry"]["id"],
                "vault_path": seeded_client.vault_path,
            },
        )
        assert response.status_code == 200
        assert response.json()["status"] == "deleted"

    def test_wrong_password_on_entry_endpoint_is_401(self, seeded_client):
        response = seeded_client.post(
            "/vault/entries",
            json={"master_password": "wrong-password", "vault_path": seeded_client.vault_path},
        )
        assert response.status_code == 401


class TestUtilityEndpoints:
    def test_generate_password(self, client):
        response = client.post("/generate", json={"kind": "password", "length": 28})
        assert response.status_code == 200
        assert len(response.json()["secret"]) == 28

    def test_generate_passphrase(self, client):
        response = client.post("/generate", json={"kind": "passphrase", "words": 5})
        assert response.status_code == 200

    def test_generate_pin(self, client):
        response = client.post("/generate", json={"kind": "pin", "length": 6})
        assert response.json()["secret"].isdigit()

    def test_generate_rejects_an_impossible_length(self, client):
        response = client.post("/generate", json={"kind": "password", "length": 3})
        assert response.status_code == 400

    def test_check_scores_a_password(self, client):
        response = client.post("/check", json={"password": "qwerty123"})
        assert response.status_code == 200
        assert response.json()["score"] < 55

    def test_check_strong_password(self, client):
        response = client.post("/check", json={"password": "Xk9#mQ2vLp7$Zw4Rt8Nb5Yc1!Gh6"})
        assert response.json()["score"] >= 80

    def test_demo_builds_a_throwaway_vault(self, client, tmp_path):
        path = str(tmp_path / "demo.json")
        response = client.post("/demo", params={"vault_path": path})
        assert response.status_code == 200
        payload = response.json()
        assert payload["master_password"] == "demo-master-password"
        assert len(payload["entries"]) >= 4
        assert 0 <= payload["audit"]["score"] <= 100
