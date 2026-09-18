"""API contract, error handling, and secret safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.stubs import StubProvider, no_op_entry, solar_entry

CASE = json.loads((Path(__file__).parent / "data" / "public_cases.json").read_text())["cases"][0]
PAYLOAD = CASE["input"]


@pytest.fixture
def client_with_stub():
    with TestClient(app) as client:
        stub = StubProvider("stub", [[solar_entry(0), no_op_entry(1)]])
        app.state.chain = [stub]
        yield client, stub


def test_health_is_ok_and_calls_no_model():
    with TestClient(app) as client:
        stub = StubProvider("stub", [])
        app.state.chain = [stub]

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
        assert stub.calls == []


def test_successful_optimization_uses_exactly_one_model_call(client_with_stub):
    client, stub = client_with_stub

    response = client.post("/optimize-energy", json=PAYLOAD)

    assert response.status_code == 200
    assert len(stub.calls) == 1


def test_response_matches_the_documented_contract(client_with_stub):
    client, _ = client_with_stub

    body = client.post("/optimize-energy", json=PAYLOAD).json()

    assert set(body) == {
        "scenario_id",
        "directive_interpretation",
        "hourly_plan",
        "total_grid_kwh",
        "total_cost_bdt",
        "peak_grid_kwh",
        "plan_summary",
    }
    assert body["scenario_id"] == PAYLOAD["scenario_id"]
    assert [entry["hour"] for entry in body["hourly_plan"]] == list(range(24))
    assert [entry["note_index"] for entry in body["directive_interpretation"]] == [0, 1]
    for entry in body["directive_interpretation"]:
        assert set(entry) == {
            "note_index",
            "applies",
            "directive_type",
            "structured_adjustment",
            "explanation",
        }
    for entry in body["hourly_plan"]:
        assert set(entry) == {
            "hour",
            "grid_kwh",
            "solar_used_kwh",
            "battery_action",
            "battery_kwh",
            "battery_energy_after_kwh",
        }
        assert entry["battery_action"] in {"charge", "discharge", "idle"}


def test_no_op_entry_keeps_the_required_shape(client_with_stub):
    client, _ = client_with_stub

    body = client.post("/optimize-energy", json=PAYLOAD).json()
    last = body["directive_interpretation"][1]

    assert last["applies"] is False
    assert last["directive_type"] == "no_op"
    assert last["structured_adjustment"] is None


def test_malformed_json_returns_400(client_with_stub):
    client, _ = client_with_stub

    response = client.post(
        "/optimize-energy",
        content=b"{not json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "mutate",
    [
        lambda p: p.update({"hours": p["hours"][:23]}),
        lambda p: p.update({"hours": p["hours"] + [dict(p["hours"][0])]}),
        lambda p: p.update({"operator_notes": []}),
        lambda p: p.update({"operator_notes": ["a", "b", "c", "d"]}),
        lambda p: p.update({"operator_notes": ["   "]}),
        lambda p: p.pop("battery"),
        lambda p: p.update({"unexpected_field": 1}),
        lambda p: p["hours"][0].update({"demand_kwh": -5}),
        lambda p: p.update({"scenario_id": ""}),
    ],
    ids=[
        "23-hours",
        "duplicate-hour",
        "zero-notes",
        "four-notes",
        "blank-note",
        "missing-battery",
        "unknown-field",
        "negative-demand",
        "empty-scenario-id",
    ],
)
def test_structurally_invalid_requests_return_422(client_with_stub, mutate):
    client, _ = client_with_stub
    payload = json.loads(json.dumps(PAYLOAD))
    mutate(payload)

    response = client.post("/optimize-energy", json=payload)

    assert response.status_code == 422


def test_error_bodies_never_leak_internals():
    with TestClient(app) as client:
        app.state.chain = []

        response = client.post("/optimize-energy", json=PAYLOAD)
        body = response.text

        assert response.status_code == 500
        assert "Traceback" not in body
        assert "api_key" not in body.lower()
        assert set(response.json()) == {"detail"}


def test_hours_may_arrive_out_of_order(client_with_stub):
    client, _ = client_with_stub
    payload = json.loads(json.dumps(PAYLOAD))
    payload["hours"] = list(reversed(payload["hours"]))

    response = client.post("/optimize-energy", json=payload)

    assert response.status_code == 200
    assert [entry["hour"] for entry in response.json()["hourly_plan"]] == list(range(24))


def test_infeasible_directives_return_422_not_a_violating_plan():
    """A directive is never softened to force a schedule."""
    impossible = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "max_grid_window",
            "structured_adjustment": {"hours": [0], "max_grid_kwh": 0.0},
            "explanation": "cap",
        },
        {
            "note_index": 1,
            "applies": True,
            "directive_type": "no_discharge_window",
            "structured_adjustment": {"hours": [0]},
            "explanation": "no discharge",
        },
    ]
    with TestClient(app) as client:
        app.state.chain = [StubProvider("stub", [impossible, impossible])]

        response = client.post("/optimize-energy", json=PAYLOAD)

        assert response.status_code == 422
        assert set(response.json()) == {"detail"}


def test_repeated_requests_stay_stable(client_with_stub):
    client, stub = client_with_stub
    stub._script = [[solar_entry(0), no_op_entry(1)] for _ in range(5)]

    codes = [client.post("/optimize-energy", json=PAYLOAD).status_code for _ in range(5)]

    assert codes == [200] * 5


def test_index_serves_the_demo_console(client_with_stub):
    """The optional browser console is served, and it leaks no configuration."""
    client, _ = client_with_stub

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "GridWise Energy Optimizer" in response.text
    for secret_name in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "api_key"):
        assert secret_name not in response.text
