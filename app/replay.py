"""Independent hour-by-hour replay of the final schedule.

This module deliberately does not reuse the optimizer's model or `SolverInputs`. It re-derives
every quantity from the original request plus the validated directives and checks the emitted plan
the way the judge does: each directive is verified directly against `hourly_plan`
(Problem Statement section 11.2), alongside the standing GridWise consistency checks of
section 11.3.

A plan that fails here is never returned.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.guardrails import ValidatedDirective
from app.schemas import HOURS_IN_DAY, Battery, HourEntry, HourlyPlanEntry

#: Official numeric tolerance (Problem Statement section 11.5).
TOLERANCE = 0.01

#: Internal safety margin: we reject well before the official tolerance is reached.
INTERNAL_TOLERANCE = 1e-4


@dataclass(frozen=True)
class ReplayReport:
    violations: list[str]

    @property
    def is_valid(self) -> bool:
        return not self.violations


def _over(value: float, limit: float, tolerance: float = INTERNAL_TOLERANCE) -> bool:
    return value > limit + tolerance


def _differs(left: float, right: float, tolerance: float = INTERNAL_TOLERANCE) -> bool:
    return abs(left - right) > tolerance


def replay(
    hours: list[HourEntry],
    battery: Battery,
    directives: list[ValidatedDirective],
    plan: list[HourlyPlanEntry],
    total_grid_kwh: float,
    total_cost_bdt: float,
    peak_grid_kwh: float,
) -> ReplayReport:
    """Validate an emitted plan against every rule in the Problem Statement."""
    violations: list[str] = []

    if len(plan) != HOURS_IN_DAY or [entry.hour for entry in plan] != list(range(HOURS_IN_DAY)):
        violations.append("hourly_plan must contain exactly 24 entries for hours 0 through 23")
        return ReplayReport(violations)

    # --- effective solar, derived independently from the directives ---------------------
    effective_solar = [entry.solar_kwh for entry in hours]
    for directive in directives:
        if directive.directive_type != "solar_reduction" or directive.adjustment is None:
            continue
        factor = float(directive.adjustment["factor"])
        for hour in directive.adjustment["hours"]:
            effective_solar[hour] = min(effective_solar[hour], hours[hour].solar_kwh * factor)

    energy = battery.initial_energy_kwh
    for hour, entry in enumerate(plan):
        demand = hours[hour].demand_kwh

        for name, value in (
            ("grid_kwh", entry.grid_kwh),
            ("solar_used_kwh", entry.solar_used_kwh),
            ("battery_kwh", entry.battery_kwh),
            ("battery_energy_after_kwh", entry.battery_energy_after_kwh),
        ):
            if value != value or value in (float("inf"), float("-inf")) or value < 0:
                violations.append(f"hour {hour}: {name} must be a finite non-negative number")

        if entry.battery_action == "idle" and _differs(entry.battery_kwh, 0.0):
            violations.append(f"hour {hour}: battery_kwh must be 0 when battery_action is idle")

        charge = entry.battery_kwh if entry.battery_action == "charge" else 0.0
        discharge = entry.battery_kwh if entry.battery_action == "discharge" else 0.0

        if _over(charge, battery.max_charge_kwh_per_hour):
            violations.append(
                f"hour {hour}: charge {charge} exceeds max_charge_kwh_per_hour "
                f"{battery.max_charge_kwh_per_hour}"
            )
        if _over(discharge, battery.max_discharge_kwh_per_hour):
            violations.append(
                f"hour {hour}: discharge {discharge} exceeds max_discharge_kwh_per_hour "
                f"{battery.max_discharge_kwh_per_hour}"
            )

        if _over(entry.solar_used_kwh, effective_solar[hour]):
            violations.append(
                f"hour {hour}: solar_used_kwh {entry.solar_used_kwh} exceeds effective solar "
                f"{effective_solar[hour]}"
            )

        supply = entry.grid_kwh + entry.solar_used_kwh + discharge
        draw = demand + charge
        if _differs(supply, draw):
            violations.append(
                f"hour {hour}: energy balance broken, supply {supply} != demand plus charge {draw}"
            )

        expected_energy = energy + charge - discharge
        if _differs(expected_energy, entry.battery_energy_after_kwh):
            violations.append(
                f"hour {hour}: battery_energy_after_kwh {entry.battery_energy_after_kwh} does not "
                f"follow from the previous level {energy} and the stated action"
            )
        energy = entry.battery_energy_after_kwh

        if _over(energy, battery.capacity_kwh):
            violations.append(f"hour {hour}: battery energy {energy} exceeds capacity")
        if _over(battery.minimum_energy_kwh, energy):
            violations.append(
                f"hour {hour}: battery energy {energy} is below the base minimum "
                f"{battery.minimum_energy_kwh}"
            )

    # --- end-of-day neutrality -----------------------------------------------------------
    if _differs(plan[-1].battery_energy_after_kwh, battery.initial_energy_kwh):
        violations.append(
            f"end of day: battery energy {plan[-1].battery_energy_after_kwh} does not return to "
            f"the initial level {battery.initial_energy_kwh}"
        )

    # --- each directive checked directly against the plan --------------------------------
    for directive in directives:
        if not directive.is_active or directive.adjustment is None:
            continue
        adjustment = directive.adjustment
        label = f"note {directive.note_index} ({directive.directive_type})"

        if directive.directive_type == "minimum_battery_reserve":
            reserve = float(adjustment["minimum_energy_kwh"])
            for hour in adjustment["hours"]:
                if _over(reserve, plan[hour].battery_energy_after_kwh):
                    violations.append(
                        f"{label}: hour {hour} battery energy "
                        f"{plan[hour].battery_energy_after_kwh} is below the required reserve "
                        f"{reserve}"
                    )
        elif directive.directive_type == "no_charge_window":
            for hour in adjustment["hours"]:
                if plan[hour].battery_action == "charge" and _differs(plan[hour].battery_kwh, 0.0):
                    violations.append(f"{label}: hour {hour} charges inside a no-charge window")
        elif directive.directive_type == "no_discharge_window":
            for hour in adjustment["hours"]:
                if plan[hour].battery_action == "discharge" and _differs(
                    plan[hour].battery_kwh, 0.0
                ):
                    violations.append(
                        f"{label}: hour {hour} discharges inside a no-discharge window"
                    )
        elif directive.directive_type == "max_grid_window":
            cap = float(adjustment["max_grid_kwh"])
            for hour in adjustment["hours"]:
                if _over(plan[hour].grid_kwh, cap):
                    violations.append(
                        f"{label}: hour {hour} grid import {plan[hour].grid_kwh} exceeds the cap "
                        f"{cap}"
                    )

    # --- reported totals must match the plan ---------------------------------------------
    recomputed_grid = sum(entry.grid_kwh for entry in plan)
    recomputed_cost = sum(entry.grid_kwh * hours[i].tariff_bdt_per_kwh for i, entry in enumerate(plan))
    recomputed_peak = max(entry.grid_kwh for entry in plan)

    if _differs(recomputed_grid, total_grid_kwh):
        violations.append(f"total_grid_kwh {total_grid_kwh} does not match the plan")
    if _differs(recomputed_cost, total_cost_bdt):
        violations.append(f"total_cost_bdt {total_cost_bdt} does not match the plan")
    if _differs(recomputed_peak, peak_grid_kwh):
        violations.append(f"peak_grid_kwh {peak_grid_kwh} does not match the plan")

    return ReplayReport(violations)
