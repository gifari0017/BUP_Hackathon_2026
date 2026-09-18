"""The failure chain: repair retry, provider failover, then a controlled error.

No path in this chain may fabricate an interpretation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.pipeline import InterpretationUnavailable, interpret_notes
from app.schemas import OptimizeRequest
from tests.stubs import (
    TRANSPORT_FAILURE,
    StubProvider,
    broken_entry,
    no_op_entry,
    solar_entry,
)

CASE = json.loads((Path(__file__).parent / "data" / "public_cases.json").read_text())["cases"][0]
REQUEST = OptimizeRequest.model_validate(CASE["input"])
GOOD = [solar_entry(0), no_op_entry(1)]
BAD = [broken_entry(0), no_op_entry(1)]


async def test_valid_first_attempt_makes_one_call():
    stub = StubProvider("primary", [GOOD])

    result = await interpret_notes([stub], REQUEST)

    assert len(stub.calls) == 1
    assert result[0].directive_type == "solar_reduction"


async def test_invalid_output_triggers_one_repair_with_explicit_feedback():
    stub = StubProvider("primary", [BAD, GOOD])

    result = await interpret_notes([stub], REQUEST, repair_attempts=1)

    assert len(stub.calls) == 2
    assert stub.calls[0]["feedback"] is None
    assert "outside the allowed range 0 to 1" in stub.calls[1]["feedback"]
    assert result[0].adjustment["factor"] == 0.25


async def test_repeated_invalid_output_fails_over_to_the_second_provider():
    primary = StubProvider("primary", [BAD, BAD])
    secondary = StubProvider("secondary", [GOOD])

    result = await interpret_notes([primary, secondary], REQUEST, repair_attempts=1)

    assert len(primary.calls) == 2
    assert len(secondary.calls) == 1
    assert result[1].directive_type == "no_op"


async def test_all_providers_invalid_raises_rather_than_fabricating():
    primary = StubProvider("primary", [BAD, BAD])
    secondary = StubProvider("secondary", [BAD, BAD])

    with pytest.raises(InterpretationUnavailable):
        await interpret_notes([primary, secondary], REQUEST, repair_attempts=1)


async def test_transport_failure_retries_then_fails_over():
    primary = StubProvider("primary", [TRANSPORT_FAILURE, TRANSPORT_FAILURE])
    secondary = StubProvider("secondary", [GOOD])

    result = await interpret_notes([primary, secondary], REQUEST, transport_retries=1)

    assert len(primary.calls) == 2
    assert len(secondary.calls) == 1
    assert len(result) == 2


async def test_transport_failure_does_not_consume_repair_attempts():
    primary = StubProvider("primary", [TRANSPORT_FAILURE, GOOD])

    result = await interpret_notes([primary], REQUEST, transport_retries=1)

    assert len(result) == 2


async def test_no_provider_configured_raises():
    with pytest.raises(InterpretationUnavailable):
        await interpret_notes([], REQUEST)


async def test_every_provider_down_raises():
    primary = StubProvider("primary", [TRANSPORT_FAILURE, TRANSPORT_FAILURE])
    secondary = StubProvider("secondary", [TRANSPORT_FAILURE, TRANSPORT_FAILURE])

    with pytest.raises(InterpretationUnavailable):
        await interpret_notes([primary, secondary], REQUEST, transport_retries=1)


def test_no_non_model_interpreter_exists_in_the_app_package():
    """Compliance guard: the language model is the only interpretation path."""
    sources = list(Path("app").rglob("*.py"))
    assert sources
    forbidden = ("fallback.py", "regex_interpreter.py", "heuristic.py")
    assert not [path for path in sources if path.name in forbidden]


# --------------------------------------------------------------------------------------
# Infeasibility is treated as a misreading, never as a reason to soften a directive.
# --------------------------------------------------------------------------------------

INFEASIBLE_REQUEST = OptimizeRequest.model_validate(
    {
        "scenario_id": "INF",
        "operator_notes": ["cap note", "discharge note"],
        "hours": [
            {"hour": h, "demand_kwh": 100.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0}
            for h in range(24)
        ],
        "battery": {
            "capacity_kwh": 200.0,
            "initial_energy_kwh": 100.0,
            "minimum_energy_kwh": 40.0,
            "max_charge_kwh_per_hour": 50.0,
            "max_discharge_kwh_per_hour": 50.0,
        },
    }
)

IMPOSSIBLE = [
    {
        "note_index": 0,
        "applies": True,
        "directive_type": "max_grid_window",
        "structured_adjustment": {"hours": [5], "max_grid_kwh": 10.0},
        "explanation": "cap",
    },
    {
        "note_index": 1,
        "applies": True,
        "directive_type": "no_discharge_window",
        "structured_adjustment": {"hours": [5]},
        "explanation": "no discharge",
    },
]

FEASIBLE = [
    {
        "note_index": 0,
        "applies": True,
        "directive_type": "max_grid_window",
        "structured_adjustment": {"hours": [5], "max_grid_kwh": 150.0},
        "explanation": "cap",
    },
    {
        "note_index": 1,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "not a directive",
    },
]


async def test_infeasible_interpretation_triggers_one_re_interpretation():
    from app import pipeline

    stub = StubProvider("primary", [IMPOSSIBLE, FEASIBLE])

    response = await pipeline.run([stub], INFEASIBLE_REQUEST)

    assert len(stub.calls) == 2
    assert "cannot all be satisfied" in stub.calls[1]["feedback"]
    assert response.hourly_plan[5].grid_kwh <= 150.0


async def test_persistent_infeasibility_raises_rather_than_softening():
    from app import pipeline
    from app.optimizer import InfeasibleScenario

    stub = StubProvider("primary", [IMPOSSIBLE, IMPOSSIBLE])

    with pytest.raises(InfeasibleScenario):
        await pipeline.run([stub], INFEASIBLE_REQUEST)


async def test_rate_limited_retry_waits_for_the_server_hint(monkeypatch):
    """A 429 retried immediately just earns another 429."""
    from app import pipeline
    from app.llm.base import RateLimited

    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(pipeline.asyncio, "sleep", fake_sleep)
    stub = StubProvider("primary", [RateLimited("rate limited", retry_after=3.0), GOOD])

    result = await interpret_notes([stub], REQUEST, transport_retries=1)

    assert slept == [3.0]
    assert len(result) == 2


async def test_rate_limit_wait_is_capped():
    from app import pipeline
    from app.llm.base import RateLimited

    assert pipeline.MAX_RETRY_WAIT_SECONDS <= 8.0
    assert RateLimited("x", retry_after=None).retry_after is None
