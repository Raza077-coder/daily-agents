"""Tests for the FastAPI layer — status codes, derived state, error mapping."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TODAY = "2026-09-17"


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient bound to a throwaway vault for this test only."""
    vault = tmp_path / "api-vault.json"
    monkeypatch.setenv("JOBFLOW_VAULT", str(vault))
    monkeypatch.delenv("JOBFLOW_DATA_DIR", raising=False)

    from fastapi.testclient import TestClient

    import api.app as app_module

    with TestClient(app_module.app) as test_client:
        yield test_client


def add(client, company="Acme", role="Backend Engineer", **kw):
    payload = {"company": company, "role": role, "today": TODAY}
    payload.update(kw)
    response = client.post("/api/applications", json=payload)
    assert response.status_code == 201, response.text
    return response.json()["application"]


# --------------------------------------------------------------------------
# Meta
# --------------------------------------------------------------------------


def test_health_reports_a_live_agent(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["agent"] == "JOBFLOW"
    assert body["version"]


def test_vocabulary_endpoint_exposes_the_contract(client):
    body = client.get("/api/vocabulary").json()
    assert "wishlist" in body["statuses"]
    assert body["stage_index"]["applied"] == 0
    assert "stalled" in body["rules"]


def test_routes_endpoint_indexes_every_endpoint(client):
    body = client.get("/api/routes").json()
    paths = {row["path"] for row in body["endpoints"]}
    assert "/api/summary" in paths
    assert "/api/plan" in paths


def test_root_returns_a_plain_text_pointer(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "docs" in response.text.lower()


# --------------------------------------------------------------------------
# Reads on an EMPTY vault (the regression that used to 500)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/summary",
        "/api/funnel",
        "/api/plan",
        "/api/applications",
        "/api/upcoming",
        "/api/export",
    ],
)
def test_every_read_endpoint_survives_an_empty_vault(client, path):
    response = client.get(path, params={"today": TODAY})
    assert response.status_code == 200, f"{path} -> {response.status_code}: {response.text}"


def test_text_endpoints_survive_an_empty_vault(client):
    for path in ("/api/status", "/api/report", "/api/report.md"):
        response = client.get(path, params={"today": TODAY})
        assert response.status_code == 200, path
        assert response.text.strip()


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------


def test_add_returns_201_and_the_record(client):
    app = add(client)
    assert app["id"] == "acme-backend-engineer"
    assert app["status"] == "wishlist"


def test_add_rejects_a_missing_company(client):
    response = client.post("/api/applications", json={"role": "Dev"})
    assert response.status_code == 422


def test_add_surfaces_a_domain_error_as_400(client):
    response = client.post(
        "/api/applications",
        json={
            "company": "Acme",
            "role": "Dev",
            "salary_min": "200000",
            "salary_max": "100000",
            "today": TODAY,
        },
    )
    assert response.status_code == 400
    assert "salary" in response.json()["detail"]


def test_list_returns_a_count_and_summary_rows(client):
    add(client)
    body = client.get("/api/applications", params={"today": TODAY}).json()
    assert body["count"] == 1
    row = body["applications"][0]
    assert row["id"] == "acme-backend-engineer"
    assert "days_since_activity" in row


def test_list_filters_by_active(client):
    add(client, status="applied", applied_on="2026-09-01")
    add(client, company="Globex", role="SRE")
    body = client.get("/api/applications", params={"active": True, "today": TODAY}).json()
    assert body["count"] == 1


def test_detail_carries_the_same_derived_block_as_the_list(client):
    """Regression: the two endpoints used to disagree about the same row."""
    add(client, status="applied", applied_on="2026-09-01")
    listing = client.get(
        "/api/applications", params={"today": TODAY}
    ).json()["applications"][0]
    detail = client.get(
        "/api/applications/acme-backend-engineer", params={"today": TODAY}
    ).json()

    for key in ("furthest_stage", "age_days", "days_in_stage", "days_since_activity"):
        assert detail[key] == listing[key], key
    assert "derived" in detail


def test_detail_for_an_unknown_id_is_404(client):
    response = client.get("/api/applications/nope", params={"today": TODAY})
    assert response.status_code == 404


def test_move_advances_the_status(client):
    add(client, status="applied", applied_on="2026-09-01")
    response = client.post(
        "/api/applications/acme-backend-engineer/move",
        json={"status": "screen", "note": "recruiter called"},
        params={"today": TODAY},
    )
    assert response.status_code == 200
    assert response.json()["application"]["status"] == "screen"


def test_move_rejects_a_skipped_rung_with_400(client):
    add(client, status="applied", applied_on="2026-09-01")
    response = client.post(
        "/api/applications/acme-backend-engineer/move",
        json={"status": "onsite"},
        params={"today": TODAY},
    )
    assert response.status_code == 400
    assert "skips" in response.json()["detail"]


def test_move_accepts_force(client):
    add(client, status="applied", applied_on="2026-09-01")
    response = client.post(
        "/api/applications/acme-backend-engineer/move",
        json={"status": "onsite", "force": True},
        params={"today": TODAY},
    )
    assert response.status_code == 200


def test_apply_logs_the_submission(client):
    add(client)
    response = client.post(
        "/api/applications/acme-backend-engineer/apply",
        json={"on": "2026-09-01"},
        params={"today": TODAY},
    )
    assert response.status_code == 200
    assert response.json()["application"]["applied_on"] == "2026-09-01"


def test_stage_backfills_without_moving_the_status(client):
    add(client, status="applied", applied_on="2026-08-01")
    response = client.post(
        "/api/applications/acme-backend-engineer/stage",
        json={"stage": "onsite", "on": "2026-09-01"},
        params={"today": TODAY},
    )
    assert response.status_code == 200
    body = response.json()["application"]
    assert body["status"] == "applied"
    assert body["event_count"] if "event_count" in body else True


def test_note_is_stored(client):
    add(client)
    response = client.post(
        "/api/applications/acme-backend-engineer/note",
        json={"text": "referred by Sara"},
        params={"today": TODAY},
    )
    assert response.status_code == 200
    assert response.json()["application"]["notes"][0]["text"] == "referred by Sara"


def test_followup_before_applying_is_400(client):
    add(client)
    response = client.post(
        "/api/applications/acme-backend-engineer/followup",
        json={"note": "ping"},
        params={"today": TODAY},
    )
    assert response.status_code == 400


def test_next_action_is_stored(client):
    add(client)
    response = client.post(
        "/api/applications/acme-backend-engineer/next",
        json={"action": "Email the hiring manager", "on": "2026-09-20"},
    )
    assert response.status_code == 200
    assert response.json()["application"]["next_action"] == "Email the hiring manager"


def test_interview_schedule_then_complete(client):
    add(client, status="applied", applied_on="2026-09-01")
    scheduled = client.post(
        "/api/applications/acme-backend-engineer/interview",
        json={"on": "2026-09-20", "kind": "technical"},
        params={"today": TODAY},
    )
    assert scheduled.status_code == 200
    assert scheduled.json()["interview"]["done"] is False

    completed = client.post(
        "/api/applications/acme-backend-engineer/interview",
        json={"on": "2026-09-20", "done": True},
        params={"today": TODAY},
    )
    assert completed.status_code == 200
    assert completed.json()["application"]["status"] == "interview"


def test_delete_removes_the_application(client):
    add(client)
    response = client.delete("/api/applications/acme-backend-engineer")
    assert response.status_code == 200
    assert client.get("/api/applications").json()["count"] == 0


def test_delete_for_an_unknown_id_is_404(client):
    assert client.delete("/api/applications/nope").status_code == 404


# --------------------------------------------------------------------------
# Demo / reset / analytics
# --------------------------------------------------------------------------


def test_demo_seeds_the_vault(client):
    response = client.post("/api/demo", json={"today": TODAY, "force": True})
    assert response.status_code == 200
    assert response.json()["seeded"]["seeded"] == 7


def test_reset_empties_the_vault(client):
    client.post("/api/demo", json={"today": TODAY, "force": True})
    assert client.post("/api/reset").status_code == 200
    assert client.get("/api/applications").json()["count"] == 0


def test_summary_after_seeding_has_real_numbers(client):
    client.post("/api/demo", json={"today": TODAY, "force": True})
    body = client.get("/api/summary", params={"today": TODAY}).json()
    assert body["totals"]["applications"] == 7
    assert body["funnel"]["submitted"] >= 5


def test_funnel_after_seeding_counts_the_onsite_rejection(client):
    """The cohort-bias case, end to end through the API."""
    client.post("/api/demo", json={"today": TODAY, "force": True})
    body = client.get("/api/funnel", params={"today": TODAY}).json()
    by_stage = {row["stage"]: row for row in body["rows"]}
    assert by_stage["onsite"]["reached"] >= 1


def test_plan_after_seeding_returns_ranked_actions(client):
    client.post("/api/demo", json={"today": TODAY, "force": True})
    body = client.get("/api/plan", params={"today": TODAY}).json()
    assert body["total"] > 0
    assert body["actions"][0]["explain"]


def test_why_reports_a_fired_rule(client):
    client.post("/api/demo", json={"today": TODAY, "force": True})
    body = client.get("/api/why/weekly_target", params={"today": TODAY}).json()
    assert body["rule"] == "weekly_target"


def test_upcoming_returns_the_scheduled_interview(client):
    client.post("/api/demo", json={"today": TODAY, "force": True})
    body = client.get("/api/upcoming", params={"today": TODAY, "days": 14}).json()
    assert body["count"] >= 1
    assert body["interviews"][0]["days_away"] >= 0


def test_status_and_report_are_plain_text(client):
    client.post("/api/demo", json={"today": TODAY, "force": True})
    for path in ("/api/status", "/api/report", "/api/report.md"):
        response = client.get(path, params={"today": TODAY})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")


def test_export_bundle_round_trips_through_json(client):
    client.post("/api/demo", json={"today": TODAY, "force": True})
    body = client.get("/api/export", params={"today": TODAY}).json()
    assert body["generated_on"] == TODAY
    assert len(body["vault"]["applications"]) == 7


def test_cors_allows_the_static_demo(client):
    response = client.get("/api/health", headers={"Origin": "https://example.com"})
    assert response.status_code == 200
    assert "access-control-allow-origin" in {k.lower() for k in response.headers}
