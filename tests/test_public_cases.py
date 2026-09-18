"""The optimizer must reach the organizer's optimal cost on every public case.

The reference interpretation is fed straight in, so this isolates optimizer and directive-
application correctness from language-model variance. Per the official equivalence note, the
hourly action sequence is not compared: only semantics, validity, and recalculated cost.
"""

from __future__ import annotations

import pytest

from app.pipeline import build_response
from app.replay import replay
from tests.conftest import directives_from_expected, load_cases, request_from_case

CASES = load_cases()
TOLERANCE = 0.01


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_matches_reference_optimal_cost(case) -> None:
    request = request_from_case(case)
    directives = directives_from_expected(case["expected_output"]["directive_interpretation"])

    response = build_response(request, directives)

    assert response.total_cost_bdt == pytest.approx(
        case["expected_output"]["total_cost_bdt"], abs=TOLERANCE
    )


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_plan_passes_independent_replay(case) -> None:
    request = request_from_case(case)
    directives = directives_from_expected(case["expected_output"]["directive_interpretation"])

    response = build_response(request, directives)
    report = replay(
        request.hours_in_order(),
        request.battery,
        directives,
        response.hourly_plan,
        response.total_grid_kwh,
        response.total_cost_bdt,
        response.peak_grid_kwh,
    )

    assert report.violations == []


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_totals_are_consistent_with_the_plan(case) -> None:
    request = request_from_case(case)
    directives = directives_from_expected(case["expected_output"]["directive_interpretation"])
    hours = request.hours_in_order()

    response = build_response(request, directives)

    assert response.total_grid_kwh == pytest.approx(
        sum(entry.grid_kwh for entry in response.hourly_plan), abs=TOLERANCE
    )
    assert response.peak_grid_kwh == pytest.approx(
        max(entry.grid_kwh for entry in response.hourly_plan), abs=TOLERANCE
    )
    assert response.total_cost_bdt == pytest.approx(
        sum(
            entry.grid_kwh * hours[index].tariff_bdt_per_kwh
            for index, entry in enumerate(response.hourly_plan)
        ),
        abs=TOLERANCE,
    )


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_battery_returns_to_the_initial_level(case) -> None:
    request = request_from_case(case)
    directives = directives_from_expected(case["expected_output"]["directive_interpretation"])

    response = build_response(request, directives)

    assert response.hourly_plan[-1].battery_energy_after_kwh == pytest.approx(
        request.battery.initial_energy_kwh, abs=TOLERANCE
    )
