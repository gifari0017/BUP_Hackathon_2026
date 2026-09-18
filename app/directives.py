"""Deterministic application of validated directives to the optimization model.

Problem Statement section 5.3 defines each directive's effect on the math. This module is the only
path by which an operator note reaches the optimizer.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.guardrails import ValidatedDirective
from app.schemas import HOURS_IN_DAY, Battery, HourEntry

INF = float("inf")


@dataclass(frozen=True)
class SolverInputs:
    """Everything the optimizer needs, with all directives already folded in."""

    demand: list[float]
    effective_solar: list[float]
    tariff: list[float]
    soc_floor: list[float]
    capacity_kwh: float
    initial_energy_kwh: float
    battery_lower: list[float]  # most negative permitted net battery flow (discharge)
    battery_upper: list[float]  # most positive permitted net battery flow (charge)
    grid_cap: list[float]


# --------------------------------------------------------------------------------------
# Same-type composition
#
# The Problem Statement defines every directive effect against a single directive and does not say
# what happens when two directives of the SAME type cover the same hour. No public sample case
# exercises it. We take the most restrictive reading, which can never be weaker than either
# directive alone. Each rule is isolated in its own function so a clarification is a one-line
# change. Cross-type composition is fully specified and needs no assumption.
# --------------------------------------------------------------------------------------


def compose_solar_factor(current: float, incoming: float) -> float:
    """ASSUMPTION (spec silent): two solar reductions on one hour -> the tighter factor wins."""
    return min(current, incoming)


def compose_reserve(current: float, incoming: float) -> float:
    """Spec-defined for base vs directive (section 5.3); extended to directive vs directive."""
    return max(current, incoming)


def compose_grid_cap(current: float, incoming: float) -> float:
    """ASSUMPTION (spec silent): two grid caps on one hour -> the tighter cap wins."""
    return min(current, incoming)


def build_solver_inputs(
    hours: list[HourEntry],
    battery: Battery,
    directives: list[ValidatedDirective],
) -> SolverInputs:
    """Fold validated directives into the numeric optimization model."""
    demand = [entry.demand_kwh for entry in hours]
    solar = [entry.solar_kwh for entry in hours]
    tariff = [entry.tariff_bdt_per_kwh for entry in hours]

    soc_floor = [battery.minimum_energy_kwh] * HOURS_IN_DAY
    lower = [-battery.max_discharge_kwh_per_hour] * HOURS_IN_DAY
    upper = [battery.max_charge_kwh_per_hour] * HOURS_IN_DAY
    grid_cap = [INF] * HOURS_IN_DAY

    for directive in directives:
        if not directive.is_active or directive.adjustment is None:
            continue
        adjustment = directive.adjustment
        directive_hours: list[int] = adjustment["hours"]

        if directive.directive_type == "solar_reduction":
            factor = float(adjustment["factor"])
            for hour in directive_hours:
                # Applied against the ORIGINAL forecast, per section 5.3, with same-type overlap
                # resolved by the composition rule rather than by repeated multiplication.
                composed = compose_solar_factor(
                    _current_factor(solar[hour], hours[hour].solar_kwh), factor
                )
                solar[hour] = hours[hour].solar_kwh * composed

        elif directive.directive_type == "minimum_battery_reserve":
            reserve = float(adjustment["minimum_energy_kwh"])
            for hour in directive_hours:
                soc_floor[hour] = compose_reserve(soc_floor[hour], reserve)

        elif directive.directive_type == "no_charge_window":
            for hour in directive_hours:
                upper[hour] = 0.0

        elif directive.directive_type == "no_discharge_window":
            for hour in directive_hours:
                lower[hour] = 0.0

        elif directive.directive_type == "max_grid_window":
            cap = float(adjustment["max_grid_kwh"])
            for hour in directive_hours:
                grid_cap[hour] = compose_grid_cap(grid_cap[hour], cap)

    return SolverInputs(
        demand=demand,
        effective_solar=solar,
        tariff=tariff,
        soc_floor=soc_floor,
        capacity_kwh=battery.capacity_kwh,
        initial_energy_kwh=battery.initial_energy_kwh,
        battery_lower=lower,
        battery_upper=upper,
        grid_cap=grid_cap,
    )


def _current_factor(effective: float, original: float) -> float:
    """Recover the factor already applied to an hour, so composition stays order-independent."""
    if original <= 0:
        return 1.0
    return effective / original
