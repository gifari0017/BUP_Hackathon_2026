"""Exact linear-programming optimizer for the 24-hour schedule.

Decision variables per hour h:

    s[h]  solar energy used,  0 <= s[h] <= effective_solar[h]   (curtailment is allowed)
    b[h]  NET battery flow,   positive = charge, negative = discharge

A single signed battery variable removes the degeneracy in which a lossless battery charges and
discharges within the same hour, which would leave `battery_action` ambiguous.

Derived quantities:

    grid_kwh[h]             g[h] = demand[h] - s[h] + b[h]
    battery_energy_after[h] E[h] = E0 + sum(b[0..h])

Objective: minimize sum(tariff[h] * g[h]) = const + sum(tariff[h] * (b[h] - s[h])).

No constraint is ever softened. An infeasible model raises `InfeasibleScenario`, because organizer
scoring scenarios are guaranteed feasible: infeasibility means our interpretation or application is
wrong, or the request is semantically invalid.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import linprog

from app.directives import SolverInputs
from app.schemas import HOURS_IN_DAY

#: Output precision. Worst-case neutrality drift is 24 * 5e-7 kWh, far inside the 0.01 tolerance.
OUTPUT_DECIMALS = 6

#: Below this magnitude a net battery flow is reported as `idle`.
IDLE_EPSILON = 1e-9

#: Residual above which we attempt a slack-checked neutrality correction.
NEUTRALITY_REPAIR_THRESHOLD = 1e-9

#: Tie-break weight that prefers using free solar when an hour's tariff is zero and the choice is
#: cost-neutral. Small enough (worst case ~5e-6 BDT over a day) that it cannot change which
#: schedule is optimal under the official 0.01 tolerance.
SOLAR_TIEBREAK = 1e-9


class InfeasibleScenario(Exception):
    """The directive set admits no valid schedule. Never softened into a violating plan."""


class SolverFailure(Exception):
    """The solver failed for a reason other than infeasibility."""


@dataclass(frozen=True)
class SolvedPlan:
    grid: list[float]
    solar_used: list[float]
    battery_net: list[float]
    battery_energy_after: list[float]


def solve(inputs: SolverInputs) -> SolvedPlan:
    """Solve the LP and return a serialization-ready plan."""
    n = 2 * HOURS_IN_DAY
    demand = np.asarray(inputs.demand, dtype=float)
    tariff = np.asarray(inputs.tariff, dtype=float)

    # cost = const + sum(tariff * b) - sum(tariff * s), plus a negligible preference for using
    # solar rather than curtailing it when the two are cost-equivalent.
    objective = np.concatenate([-(tariff + SOLAR_TIEBREAK), tariff])

    rows: list[np.ndarray] = []
    rhs: list[float] = []

    for hour in range(HOURS_IN_DAY):
        # grid >= 0   <=>   s[h] - b[h] <= demand[h]
        row = np.zeros(n)
        row[hour] = 1.0
        row[HOURS_IN_DAY + hour] = -1.0
        rows.append(row)
        rhs.append(demand[hour])

        # grid <= cap   <=>   -s[h] + b[h] <= cap - demand[h]
        cap = inputs.grid_cap[hour]
        if math.isfinite(cap):
            row = np.zeros(n)
            row[hour] = -1.0
            row[HOURS_IN_DAY + hour] = 1.0
            rows.append(row)
            rhs.append(cap - demand[hour])

        # state of charge bounds on E[h] = E0 + cumulative b
        cumulative = np.zeros(n)
        cumulative[HOURS_IN_DAY : HOURS_IN_DAY + hour + 1] = 1.0
        rows.append(cumulative.copy())
        rhs.append(inputs.capacity_kwh - inputs.initial_energy_kwh)
        rows.append(-cumulative)
        rhs.append(inputs.initial_energy_kwh - inputs.soc_floor[hour])

    # end-of-day neutrality: sum(b) == 0
    equality = np.concatenate([np.zeros(HOURS_IN_DAY), np.ones(HOURS_IN_DAY)])

    bounds = [(0.0, float(value)) for value in inputs.effective_solar] + [
        (float(low), float(high))
        for low, high in zip(inputs.battery_lower, inputs.battery_upper, strict=True)
    ]

    for low, high in bounds:
        if low > high:
            raise InfeasibleScenario("a directive makes a variable's own bounds contradictory")

    result = linprog(
        objective,
        A_ub=np.asarray(rows),
        b_ub=np.asarray(rhs),
        A_eq=equality.reshape(1, -1),
        b_eq=np.asarray([0.0]),
        bounds=bounds,
        method="highs",
    )

    if not result.success:
        # status 2 is "infeasible" for the HiGHS wrapper; treat 3 (unbounded) as a solver failure.
        if getattr(result, "status", None) == 2:
            raise InfeasibleScenario(result.message)
        raise SolverFailure(result.message)

    solar_used = [float(value) for value in result.x[:HOURS_IN_DAY]]
    battery_net = [float(value) for value in result.x[HOURS_IN_DAY:]]
    return _materialize(inputs, solar_used, battery_net)


def _materialize(
    inputs: SolverInputs, solar_used: list[float], battery_net: list[float]
) -> SolvedPlan:
    """Round for output, then derive every other number from the rounded values.

    Deriving grid, state of charge, and the totals from the serialized figures makes the emitted
    plan internally consistent by construction rather than by floating-point luck.
    """
    solar_out = [round(max(0.0, value), OUTPUT_DECIMALS) for value in solar_used]
    battery_out = [round(value, OUTPUT_DECIMALS) for value in battery_net]

    residual = math.fsum(battery_out)
    if abs(residual) > NEUTRALITY_REPAIR_THRESHOLD:
        battery_out = _repair_neutrality(inputs, solar_out, battery_out, residual)

    grid_out: list[float] = []
    energy_out: list[float] = []
    energy = inputs.initial_energy_kwh
    for hour in range(HOURS_IN_DAY):
        grid = inputs.demand[hour] - solar_out[hour] + battery_out[hour]
        grid_out.append(round(max(0.0, grid), OUTPUT_DECIMALS))
        energy = round(energy + battery_out[hour], OUTPUT_DECIMALS)
        energy_out.append(energy)

    return SolvedPlan(
        grid=grid_out,
        solar_used=solar_out,
        battery_net=battery_out,
        battery_energy_after=energy_out,
    )


def _repair_neutrality(
    inputs: SolverInputs,
    solar_out: list[float],
    battery_out: list[float],
    residual: float,
) -> list[float]:
    """Remove a rounding residual, but only in an hour with slack in EVERY affected constraint.

    A blind adjustment could violate a rate limit, a no-charge or no-discharge window, an active
    reserve floor, the capacity ceiling, or a grid cap. So each candidate hour is checked against
    all of them, including the state of charge for every later hour, since a change at hour h
    shifts all subsequent energy levels. If no hour qualifies the residual is left in place and the
    replay validator decides whether the plan is still inside tolerance.
    """
    delta = -residual

    energy_before: list[float] = []
    energy = inputs.initial_energy_kwh
    for hour in range(HOURS_IN_DAY):
        energy += battery_out[hour]
        energy_before.append(energy)

    for hour in range(HOURS_IN_DAY):
        candidate = battery_out[hour] + delta
        if candidate < inputs.battery_lower[hour] or candidate > inputs.battery_upper[hour]:
            continue

        grid = inputs.demand[hour] - solar_out[hour] + candidate
        if grid < 0.0 or grid > inputs.grid_cap[hour]:
            continue

        if any(
            energy_before[later] + delta < inputs.soc_floor[later]
            or energy_before[later] + delta > inputs.capacity_kwh
            for later in range(hour, HOURS_IN_DAY)
        ):
            continue

        repaired = list(battery_out)
        repaired[hour] = round(candidate, OUTPUT_DECIMALS)
        return repaired

    return battery_out


def battery_action_for(net: float) -> tuple[str, float]:
    """Map a net battery flow onto the reported action and its non-negative magnitude."""
    if net > IDLE_EPSILON:
        return "charge", abs(net)
    if net < -IDLE_EPSILON:
        return "discharge", abs(net)
    return "idle", 0.0
