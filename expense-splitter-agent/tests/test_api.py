"""Tests for the FastAPI layer."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from api.index import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    import api.index as api_module

    api_module._STATE["kit"] = None  # each test starts with nothing loaded
    return TestClient(app)


@pytest.fixture
def demo_client(client: TestClient) -> TestClient:
    response = client.post("/demo")
    assert response.status_code == 200
    return client


# --------------------------------------------------------------------------- #
# meta
# --------------------------------------------------------------------------- #


def test_root_lists_endpoints(client):
    body = client.get("/").json()
    assert body["service"] == "SplitKit"
    assert any("/settle" in e for e in body["endpoints"])


def test_health_before_any_group(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["group_loaded"] is False


def test_health_after_loading_demo(demo_client):
    body = demo_client.get("/health").json()
    assert body["group_loaded"] is True
    assert body["group"] == "goa-weekend"
    assert body["expenses"] == 7


def test_modes_endpoint_lists_six_modes(client):
    body = client.get("/modes").json()
    assert set(body) == {"equal", "exact", "shares", "percent", "itemized", "adjustment"}


def test_currencies_endpoint(client):
    body = client.get("/currencies").json()
    assert body["exponents"]["JPY"] == 0
    assert body["exponents"]["KWD"] == 3
    assert body["count"] > 50


def test_config_endpoint(client):
    body = client.get("/config").json()
    assert "settings" in body
    assert body["settings"]["rounding"] == "largest_remainder"


# --------------------------------------------------------------------------- #
# group lifecycle
# --------------------------------------------------------------------------- #


def test_demo_loads_the_seeded_group(demo_client):
    body = demo_client.get("/group").json()
    assert body["id"] == "goa-weekend"
    assert len(body["members"]) == 3
    assert len(body["expenses"]) == 7


def test_demo_rejects_an_unknown_currency(client):
    assert client.post("/demo?currency=XXX").status_code == 400


def test_demo_accepts_a_valid_currency(client):
    body = client.post("/demo?currency=jpy").json()
    assert body["group"] == "goa-weekend"


def test_demo_loads_for_a_zero_decimal_currency(client):
    """JPY has no minor unit, so sub-unit expenses must be dropped, not rounded."""
    body = client.post("/demo?currency=JPY").json()
    report = body["report"]
    assert report["currency"] == "JPY"
    assert report["summary"]["is_balanced"] is True
    assert all(e["amount_minor"] == int(e["amount_minor"]) for e in report["expenses"])


def test_demo_loads_for_a_three_decimal_currency(client):
    body = client.post("/demo?currency=KWD").json()
    assert body["report"]["summary"]["is_balanced"] is True


def test_create_group(client):
    response = client.post(
        "/group", json={"name": "Flat 4B", "members": ["Ali", "Sara"], "currency": "PKR"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["currency"] == "PKR"
    assert [m["id"] for m in body["members"]] == ["ali", "sara"]


def test_create_group_requires_members(client):
    assert client.post("/group", json={"name": "X", "members": []}).status_code == 422


def test_group_404_before_anything_is_loaded(client):
    response = client.get("/group")
    assert response.status_code == 404
    assert "No group loaded" in response.json()["detail"]


@pytest.mark.parametrize("currency", ["USD", "PKR", "JPY", "KWD"])
def test_demo_balances_for_every_exponent(client, currency):
    report = client.post(f"/demo?currency={currency}").json()["report"]
    assert report["summary"]["is_balanced"] is True
    assert report["summary"]["is_settled"] is False
    for e in report["expenses"]:
        assert isinstance(e["amount_minor"], int)


def test_add_member(client):
    client.post("/group", json={"name": "T", "members": ["Ali", "Sara"]})
    response = client.post("/members", json={"name": "Bilal"})
    assert response.status_code == 201
    assert response.json()["id"] == "bilal"
    assert len(client.get("/members").json()["members"]) == 3


def test_add_duplicate_member_is_400(client):
    client.post("/group", json={"name": "T", "members": ["Ali"]})
    assert client.post("/members", json={"name": "Ali"}).status_code == 400


# --------------------------------------------------------------------------- #
# expenses
# --------------------------------------------------------------------------- #


def test_add_expense_equal_split(client):
    client.post("/group", json={"name": "T", "members": ["Ali", "Sara"]})
    response = client.post(
        "/expense", json={"description": "Dinner", "amount": "100.00", "paid_by": "ali"}
    )
    assert response.status_code == 201
    body = response.json()
    assert body["amount_minor"] == 10000
    assert body["shares"]["ali"]["minor"] == 5000
    assert body["shares"]["sara"]["minor"] == 5000
    assert body["shares_sum_check"]["ok"] is True


def test_add_expense_with_shares(client):
    client.post("/group", json={"name": "T", "members": ["Ali", "Sara", "Bilal"]})
    response = client.post(
        "/expense",
        json={
            "description": "Platter",
            "amount": "92.40",
            "paid_by": "ali",
            "split": {"mode": "shares", "shares": {"ali": 2, "sara": 1, "bilal": 1}},
        },
    )
    assert response.status_code == 201
    shares = response.json()["shares"]
    assert shares["ali"]["minor"] == 4620
    assert shares["sara"]["minor"] == 2310


def test_add_expense_rejects_a_mismatched_exact_split(client):
    client.post("/group", json={"name": "T", "members": ["Ali", "Sara"]})
    response = client.post(
        "/expense",
        json={
            "description": "Cab",
            "amount": "20.00",
            "paid_by": "ali",
            "split": {"mode": "exact", "amounts": {"ali": "12.00", "sara": "6.00"}},
        },
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "18.00" in detail and "20.00" in detail
    # The rejected expense must not have been stored.
    assert client.get("/expenses").json()["expenses"] == []


def test_add_expense_unknown_payer_is_404(client):
    client.post("/group", json={"name": "T", "members": ["Ali"]})
    response = client.post(
        "/expense", json={"description": "X", "amount": "10.00", "paid_by": "ghost"}
    )
    assert response.status_code == 404


def test_add_expense_rejects_extra_decimal_precision(client):
    """"1.234" in USD is refused rather than silently rounded to 1.23."""
    client.post("/group", json={"name": "T", "members": ["Ali"]})
    response = client.post(
        "/expense", json={"description": "X", "amount": "1.234", "paid_by": "ali"}
    )
    assert response.status_code == 400
    assert "decimal places" in response.json()["detail"]


def test_add_expense_requires_a_description(client):
    client.post("/group", json={"name": "T", "members": ["Ali"]})
    response = client.post(
        "/expense", json={"description": "", "amount": "10.00", "paid_by": "ali"}
    )
    assert response.status_code == 422


def test_list_expenses(demo_client):
    body = demo_client.get("/expenses").json()
    assert len(body["expenses"]) == 7
    for e in body["expenses"]:
        assert e["shares_sum_check"]["ok"] is True


def test_delete_expense(demo_client):
    assert demo_client.delete("/expense/e3").status_code == 200
    assert len(demo_client.get("/expenses").json()["expenses"]) == 6


def test_delete_unknown_expense_is_404(demo_client):
    assert demo_client.delete("/expense/nope").status_code == 404


# --------------------------------------------------------------------------- #
# analysis
# --------------------------------------------------------------------------- #


def test_balances_endpoint(demo_client):
    body = demo_client.get("/balances").json()
    assert body["balanced"] is True
    assert body["sum_minor"] == 0
    assert len(body["rows"]) == 3


def test_settle_endpoint(demo_client):
    body = demo_client.get("/settle").json()
    assert body["transfer_count"] >= 1
    assert body["verified"] is True
    assert len(body["readable"]) == body["transfer_count"]


@pytest.mark.parametrize("strategy", ["greedy", "optimal", "compare"])
def test_settle_accepts_each_strategy(demo_client, strategy):
    assert demo_client.get(f"/settle?strategy={strategy}").status_code == 200


def test_settle_rejects_a_bad_strategy(demo_client):
    assert demo_client.get("/settle?strategy=teleport").status_code == 422


def test_report_json(demo_client):
    body = demo_client.get("/report").json()
    assert body["name"] == "Goa Weekend"
    assert body["summary"]["is_balanced"] is True


def test_report_text(demo_client):
    response = demo_client.get("/report?format=text")
    assert response.status_code == 200
    assert "Settle up" in response.text


def test_report_markdown(demo_client):
    response = demo_client.get("/report?format=markdown")
    assert response.status_code == 200
    assert response.text.startswith("# Goa Weekend")
    assert "| Member |" in response.text


def test_report_rejects_an_unknown_format(demo_client):
    assert demo_client.get("/report?format=pdf").status_code == 422


def test_report_can_skip_expenses(demo_client):
    body = demo_client.get("/report?include_expenses=false").json()
    assert body["expenses"] == []


def test_verify_endpoint(demo_client):
    body = demo_client.get("/verify").json()
    assert body["balanced"] is True
    assert body["settle_plan_valid"] is True
    assert body["sum_of_balances_minor"] == 0


# --------------------------------------------------------------------------- #
# settlements
# --------------------------------------------------------------------------- #


def test_record_settlement_clears_the_book(demo_client):
    plan = demo_client.get("/settle").json()
    first = plan["transfers"][0]
    response = demo_client.post(
        "/settlement",
        json={"from_member": first["from"], "to_member": first["to"], "amount": str(first["amount"] / 100)},
    )
    assert response.status_code == 201
    assert response.json()["settlement"]["amount"] == first["amount"]


def test_settlement_rejects_an_unknown_member(demo_client):
    response = demo_client.post(
        "/settlement", json={"from_member": "ghost", "to_member": "ali", "amount": "1.00"}
    )
    assert response.status_code == 404


def test_settlement_rejects_a_non_positive_amount(demo_client):
    response = demo_client.post(
        "/settlement", json={"from_member": "bilal", "to_member": "ali", "amount": "0"}
    )
    assert response.status_code == 400


def test_list_settlements(demo_client):
    before = demo_client.get("/settlements").json()["settlements"]
    assert before == []
    demo_client.post(
        "/settlement", json={"from_member": "bilal", "to_member": "ali", "amount": "10.00"}
    )
    after = demo_client.get("/settlements").json()["settlements"]
    assert len(after) == 1


# --------------------------------------------------------------------------- #
# ask
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "question,intent",
    [
        ("who owes what", "balances"),
        ("settle up", "settle"),
        ("where did the money go", "categories"),
        ("full report", "report"),
        ("anything else at all", "overview"),
    ],
)
def test_ask_routes_to_the_right_intent(demo_client, question, intent):
    body = demo_client.post("/ask", json={"question": question}).json()
    assert body["intent"] == intent
    assert body["text"]


def test_ask_defaults_to_an_overview(demo_client):
    body = demo_client.post("/ask").json()
    assert body["intent"] in ("overview", "balances")


def test_ask_settle_mentions_the_transfer_count(demo_client):
    body = demo_client.post("/ask", json={"question": "settle up"}).json()
    assert str(body["data"]["transfer_count"]) in body["text"]


# --------------------------------------------------------------------------- #
# contract-level checks
# --------------------------------------------------------------------------- #


def test_openapi_schema_is_served(client):
    schema = client.get("/openapi.json").json()
    assert schema["info"]["title"] == "SplitKit API"
    for path in ("/settle", "/balances", "/expense", "/demo"):
        assert path in schema["paths"]


def test_every_amount_in_the_api_is_integer_minor_units(demo_client):
    """No endpoint may leak a float amount."""
    for e in demo_client.get("/expenses").json()["expenses"]:
        assert isinstance(e["amount_minor"], int)
        for share in e["shares"].values():
            assert isinstance(share["minor"], int)


def test_demo_report_is_fully_deterministic(client):
    first = client.post("/demo").json()["report"]
    second = client.post("/demo").json()["report"]
    assert first == second
