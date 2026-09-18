"""Directive application, including the documented same-type composition assumptions."""

from __future__ import annotations

from app.directives import build_solver_inputs
from app.guardrails import ValidatedDirective
from app.schemas import Battery, HourEntry

BATTERY = Battery(
    capacity_kwh=200.0,
    initial_energy_kwh=100.0,
    minimum_energy_kwh=40.0,
    max_charge_kwh_per_hour=50.0,
    max_discharge_kwh_per_hour=50.0,
)
HOURS = [
    HourEntry(hour=h, demand_kwh=100.0, solar_kwh=80.0, tariff_bdt_per_kwh=5.0) for h in range(24)
]


def test_solar_reduction_scales_the_original_forecast():
    directives = [ValidatedDirective(0, True, "solar_reduction", {"hours": [10], "factor": 0.25}, "")]

    inputs = build_solver_inputs(HOURS, BATTERY, directives)

    assert inputs.effective_solar[10] == 20.0
    assert inputs.effective_solar[11] == 80.0


def test_overlapping_solar_reductions_take_the_tighter_factor():
    """ASSUMPTION (spec silent): most restrictive wins, and the result is order independent."""
    directives = [
        ValidatedDirective(0, True, "solar_reduction", {"hours": [10], "factor": 0.5}, ""),
        ValidatedDirective(1, True, "solar_reduction", {"hours": [10], "factor": 0.2}, ""),
    ]

    forward = build_solver_inputs(HOURS, BATTERY, directives)
    reverse = build_solver_inputs(HOURS, BATTERY, list(reversed(directives)))

    assert forward.effective_solar[10] == 16.0
    assert reverse.effective_solar[10] == 16.0


def test_reserve_raises_the_floor_above_the_base_minimum():
    directives = [
        ValidatedDirective(
            0, True, "minimum_battery_reserve", {"hours": [18], "minimum_energy_kwh": 120.0}, ""
        )
    ]

    inputs = build_solver_inputs(HOURS, BATTERY, directives)

    assert inputs.soc_floor[18] == 120.0
    assert inputs.soc_floor[17] == BATTERY.minimum_energy_kwh


def test_a_reserve_below_the_base_minimum_never_lowers_the_floor():
    directives = [
        ValidatedDirective(
            0, True, "minimum_battery_reserve", {"hours": [18], "minimum_energy_kwh": 10.0}, ""
        )
    ]

    inputs = build_solver_inputs(HOURS, BATTERY, directives)

    assert inputs.soc_floor[18] == BATTERY.minimum_energy_kwh


def test_overlapping_grid_caps_take_the_tighter_cap():
    directives = [
        ValidatedDirective(0, True, "max_grid_window", {"hours": [19], "max_grid_kwh": 180.0}, ""),
        ValidatedDirective(1, True, "max_grid_window", {"hours": [19], "max_grid_kwh": 150.0}, ""),
    ]

    inputs = build_solver_inputs(HOURS, BATTERY, directives)

    assert inputs.grid_cap[19] == 150.0


def test_windows_union_and_bound_the_signed_battery_variable():
    directives = [
        ValidatedDirective(0, True, "no_charge_window", {"hours": [2, 3]}, ""),
        ValidatedDirective(1, True, "no_discharge_window", {"hours": [3, 4]}, ""),
    ]

    inputs = build_solver_inputs(HOURS, BATTERY, directives)

    assert inputs.battery_upper[2] == 0.0
    assert inputs.battery_upper[3] == 0.0
    assert inputs.battery_lower[3] == 0.0
    assert inputs.battery_lower[4] == 0.0
    assert inputs.battery_upper[5] == BATTERY.max_charge_kwh_per_hour


def test_no_op_changes_nothing():
    directives = [ValidatedDirective(0, False, "no_op", None, "")]

    inputs = build_solver_inputs(HOURS, BATTERY, directives)

    assert inputs.effective_solar == [80.0] * 24
    assert inputs.soc_floor == [BATTERY.minimum_energy_kwh] * 24
    assert all(cap == float("inf") for cap in inputs.grid_cap)
