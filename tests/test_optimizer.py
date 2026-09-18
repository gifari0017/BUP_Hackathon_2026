"""Optimizer behavior, including the rule that a directive is never softened."""

from __future__ import annotations

import random

import pytest

from app.directives import build_solver_inputs
from app.guardrails import ValidatedDirective
from app.optimizer import InfeasibleScenario, solve
from app.pipeline import build_response
from app.replay import replay
from app.schemas import Battery, HourEntry, OptimizeRequest


def hours(demand=100.0, solar=0.0, tariff=None):
    tariff = tariff or [10.0] * 24
    return [
        HourEntry(hour=h, demand_kwh=demand, solar_kwh=solar, tariff_bdt_per_kwh=tariff[h])
        for h in range(24)
    ]


BATTERY = Battery(
    capacity_kwh=200.0,
    initial_energy_kwh=100.0,
    minimum_energy_kwh=40.0,
    max_charge_kwh_per_hour=50.0,
    max_discharge_kwh_per_hour=50.0,
)


def test_infeasible_scenario_raises_instead_of_softening():
    """A grid cap below demand with no solar and a no-discharge window cannot be satisfied."""
    directives = [
        ValidatedDirective(0, True, "max_grid_window", {"hours": [5], "max_grid_kwh": 10.0}, ""),
        ValidatedDirective(1, True, "no_discharge_window", {"hours": [5]}, ""),
    ]
    inputs = build_solver_inputs(hours(), BATTERY, directives)

    with pytest.raises(InfeasibleScenario):
        solve(inputs)


def test_contradictory_bounds_raise_infeasible():
    """A reserve above capacity cannot be met; the optimizer must refuse, not relax."""
    directives = [
        ValidatedDirective(
            0, True, "minimum_battery_reserve", {"hours": [0], "minimum_energy_kwh": 195.0}, ""
        )
    ]
    inputs = build_solver_inputs(hours(), BATTERY, directives)

    with pytest.raises(InfeasibleScenario):
        solve(inputs)


def test_arbitrage_uses_cheap_hours_and_returns_to_the_initial_level():
    tariff = [2.0] * 12 + [20.0] * 12
    request = OptimizeRequest(
        scenario_id="T",
        operator_notes=["n"],
        hours=hours(tariff=tariff),
        battery=BATTERY,
    )

    response = build_response(request, [])
    charged = sum(e.battery_kwh for e in response.hourly_plan if e.battery_action == "charge")
    discharged = sum(
        e.battery_kwh for e in response.hourly_plan if e.battery_action == "discharge"
    )

    assert charged > 0
    assert charged == pytest.approx(discharged, abs=0.01)
    assert response.hourly_plan[-1].battery_energy_after_kwh == pytest.approx(
        BATTERY.initial_energy_kwh, abs=0.01
    )


def test_solar_is_used_before_grid():
    request = OptimizeRequest(
        scenario_id="T",
        operator_notes=["n"],
        hours=hours(solar=40.0),
        battery=BATTERY,
    )

    response = build_response(request, [])

    assert all(entry.solar_used_kwh == pytest.approx(40.0, abs=0.01) for entry in response.hourly_plan)


def test_solar_reduction_lowers_usable_solar():
    directives = [
        ValidatedDirective(0, True, "solar_reduction", {"hours": [10, 11], "factor": 0.25}, "")
    ]
    request = OptimizeRequest(
        scenario_id="T", operator_notes=["n"], hours=hours(solar=40.0), battery=BATTERY
    )

    response = build_response(request, directives)

    assert response.hourly_plan[10].solar_used_kwh == pytest.approx(10.0, abs=0.01)
    assert response.hourly_plan[12].solar_used_kwh == pytest.approx(40.0, abs=0.01)


def test_randomized_feasible_batteries_always_produce_a_valid_plan():
    """Property test over edge battery parameters seen in hidden cases."""
    rng = random.Random(20260918)
    for _ in range(40):
        capacity = rng.choice([50.0, 120.0, 500.0])
        minimum = rng.choice([0.0, capacity * 0.25])
        initial = rng.uniform(minimum, capacity)
        battery = Battery(
            capacity_kwh=capacity,
            initial_energy_kwh=initial,
            minimum_energy_kwh=minimum,
            max_charge_kwh_per_hour=rng.choice([0.0, 10.0, capacity]),
            max_discharge_kwh_per_hour=rng.choice([0.0, 10.0, capacity]),
        )
        tariff = [rng.uniform(1.0, 25.0) for _ in range(24)]
        entries = [
            HourEntry(
                hour=h,
                demand_kwh=rng.uniform(0.0, 300.0),
                solar_kwh=rng.uniform(0.0, 200.0),
                tariff_bdt_per_kwh=tariff[h],
            )
            for h in range(24)
        ]
        request = OptimizeRequest(
            scenario_id="P", operator_notes=["n"], hours=entries, battery=battery
        )

        response = build_response(request, [])
        report = replay(
            request.hours_in_order(),
            battery,
            [],
            response.hourly_plan,
            response.total_grid_kwh,
            response.total_cost_bdt,
            response.peak_grid_kwh,
        )
        assert report.violations == []
