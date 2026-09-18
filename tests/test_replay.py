"""The replay validator must reject any plan that breaks a GridWise rule or a directive."""

from __future__ import annotations

import pytest

from app.guardrails import ValidatedDirective
from app.replay import replay
from app.schemas import Battery, HourEntry, HourlyPlanEntry

BATTERY = Battery(
    capacity_kwh=200.0,
    initial_energy_kwh=100.0,
    minimum_energy_kwh=40.0,
    max_charge_kwh_per_hour=50.0,
    max_discharge_kwh_per_hour=50.0,
)


def flat_hours(demand: float = 100.0, solar: float = 0.0, tariff: float = 10.0):
    return [
        HourEntry(hour=h, demand_kwh=demand, solar_kwh=solar, tariff_bdt_per_kwh=tariff)
        for h in range(24)
    ]


def idle_plan(demand: float = 100.0):
    return [
        HourlyPlanEntry(
            hour=h,
            grid_kwh=demand,
            solar_used_kwh=0.0,
            battery_action="idle",
            battery_kwh=0.0,
            battery_energy_after_kwh=BATTERY.initial_energy_kwh,
        )
        for h in range(24)
    ]


def check(hours, plan, directives=None):
    total_grid = sum(e.grid_kwh for e in plan)
    total_cost = sum(e.grid_kwh * hours[i].tariff_bdt_per_kwh for i, e in enumerate(plan))
    peak = max(e.grid_kwh for e in plan)
    return replay(hours, BATTERY, directives or [], plan, total_grid, total_cost, peak)


def test_accepts_a_valid_idle_plan():
    assert check(flat_hours(), idle_plan()).violations == []


def test_rejects_energy_balance_failure():
    plan = idle_plan()
    plan[5] = plan[5].model_copy(update={"grid_kwh": 50.0})

    assert any("energy balance" in v for v in check(flat_hours(), plan).violations)


def test_rejects_solar_overuse_after_a_reduction():
    hours = flat_hours(solar=80.0)
    plan = idle_plan()
    plan[12] = plan[12].model_copy(update={"grid_kwh": 20.0, "solar_used_kwh": 80.0})
    directive = ValidatedDirective(0, True, "solar_reduction", {"hours": [12], "factor": 0.25}, "")

    violations = check(hours, plan, [directive]).violations

    assert any("exceeds effective solar" in v for v in violations)


def test_rejects_charging_inside_a_no_charge_window():
    plan = idle_plan()
    plan[3] = plan[3].model_copy(
        update={
            "grid_kwh": 130.0,
            "battery_action": "charge",
            "battery_kwh": 30.0,
            "battery_energy_after_kwh": 130.0,
        }
    )
    for hour in range(4, 24):
        plan[hour] = plan[hour].model_copy(update={"battery_energy_after_kwh": 130.0})
    directive = ValidatedDirective(0, True, "no_charge_window", {"hours": [3]}, "")

    violations = check(flat_hours(), plan, [directive]).violations

    assert any("no-charge window" in v for v in violations)


def test_rejects_discharging_inside_a_no_discharge_window():
    plan = idle_plan()
    plan[18] = plan[18].model_copy(
        update={
            "grid_kwh": 70.0,
            "battery_action": "discharge",
            "battery_kwh": 30.0,
            "battery_energy_after_kwh": 70.0,
        }
    )
    for hour in range(19, 24):
        plan[hour] = plan[hour].model_copy(update={"battery_energy_after_kwh": 70.0})
    directive = ValidatedDirective(0, True, "no_discharge_window", {"hours": [18]}, "")

    violations = check(flat_hours(), plan, [directive]).violations

    assert any("no-discharge window" in v for v in violations)


def test_rejects_a_reserve_violation():
    plan = idle_plan()
    directive = ValidatedDirective(
        0, True, "minimum_battery_reserve", {"hours": [18], "minimum_energy_kwh": 150.0}, ""
    )

    violations = check(flat_hours(), plan, [directive]).violations

    assert any("below the required reserve" in v for v in violations)


def test_rejects_a_grid_cap_violation():
    plan = idle_plan()
    directive = ValidatedDirective(
        0, True, "max_grid_window", {"hours": [19], "max_grid_kwh": 50.0}, ""
    )

    violations = check(flat_hours(), plan, [directive]).violations

    assert any("exceeds the cap" in v for v in violations)


def test_rejects_broken_end_of_day_neutrality():
    plan = idle_plan()
    plan[23] = plan[23].model_copy(
        update={
            "grid_kwh": 70.0,
            "battery_action": "discharge",
            "battery_kwh": 30.0,
            "battery_energy_after_kwh": 70.0,
        }
    )

    assert any("does not return to" in v for v in check(flat_hours(), plan).violations)


def test_rejects_idle_with_a_non_zero_magnitude():
    plan = idle_plan()
    plan[7] = plan[7].model_copy(update={"battery_kwh": 5.0})

    assert any("must be 0 when battery_action is idle" in v for v in check(flat_hours(), plan).violations)


def test_rejects_a_rate_limit_violation():
    plan = idle_plan()
    plan[2] = plan[2].model_copy(
        update={
            "grid_kwh": 180.0,
            "battery_action": "charge",
            "battery_kwh": 80.0,
            "battery_energy_after_kwh": 180.0,
        }
    )
    for hour in range(3, 24):
        plan[hour] = plan[hour].model_copy(update={"battery_energy_after_kwh": 180.0})

    assert any("max_charge_kwh_per_hour" in v for v in check(flat_hours(), plan).violations)


def test_rejects_a_state_of_charge_transition_that_does_not_follow():
    plan = idle_plan()
    plan[4] = plan[4].model_copy(update={"battery_energy_after_kwh": 150.0})

    assert any("does not follow" in v for v in check(flat_hours(), plan).violations)


def test_rejects_capacity_overflow():
    plan = idle_plan()
    for hour in range(24):
        plan[hour] = plan[hour].model_copy(update={"battery_energy_after_kwh": 250.0})

    assert any("exceeds capacity" in v for v in check(flat_hours(), plan).violations)


def test_rejects_totals_that_disagree_with_the_plan():
    hours = flat_hours()
    plan = idle_plan()

    report = replay(hours, BATTERY, [], plan, 1.0, 2.0, 3.0)

    assert any("total_grid_kwh" in v for v in report.violations)
    assert any("total_cost_bdt" in v for v in report.violations)
    assert any("peak_grid_kwh" in v for v in report.violations)


def test_rejects_a_plan_with_the_wrong_number_of_hours():
    hours = flat_hours()
    plan = idle_plan()[:23]

    report = replay(hours, BATTERY, [], plan, 0.0, 0.0, 0.0)

    assert report.violations
