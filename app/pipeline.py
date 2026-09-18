"""Request orchestration: interpretation, optimization, replay, response assembly."""

from __future__ import annotations

import logging

from app.directives import build_solver_inputs
from app.guardrails import (
    InterpretationInvalid,
    ValidatedDirective,
    validate_interpretation,
)
from app.llm.base import LLMProvider, ProviderError
from app.optimizer import (
    InfeasibleScenario,
    SolverFailure,
    battery_action_for,
    solve,
)
from app.replay import replay
from app.schemas import (
    DirectiveInterpretation,
    HourlyPlanEntry,
    OptimizeRequest,
    OptimizeResponse,
)

logger = logging.getLogger("gridwise.pipeline")

TOTALS_DECIMALS = 6


class InterpretationUnavailable(Exception):
    """Every configured language-model provider failed. No interpretation is fabricated."""


class PlanRejected(Exception):
    """The independent replay rejected our own plan. Never returned to the caller."""


async def interpret_notes(
    chain: list[LLMProvider],
    request: OptimizeRequest,
    repair_attempts: int = 1,
    transport_retries: int = 1,
    initial_feedback: str | None = None,
) -> list[ValidatedDirective]:
    """Run the interpretation chain: provider -> bounded repair -> next provider -> give up."""
    if not chain:
        raise InterpretationUnavailable("no language-model provider is configured")

    failures: list[str] = []

    for provider in chain:
        feedback: str | None = initial_feedback
        for attempt in range(repair_attempts + 1):
            try:
                raw = await _call_with_transport_retry(
                    provider, request, feedback, transport_retries
                )
            except ProviderError as exc:
                failures.append(f"{provider.name}: {exc}")
                break

            try:
                return validate_interpretation(
                    raw.entries, request.operator_notes, request.battery
                )
            except InterpretationInvalid as exc:
                failures.append(f"{provider.name} attempt {attempt + 1}: {exc}")
                feedback = "\n".join(f"- {message}" for message in exc.errors)
                logger.warning(
                    "interpretation rejected by guardrails",
                    extra={"provider": provider.name, "attempt": attempt + 1},
                )

    raise InterpretationUnavailable("; ".join(failures) or "interpretation failed")


async def _call_with_transport_retry(
    provider: LLMProvider,
    request: OptimizeRequest,
    feedback: str | None,
    retries: int,
):
    last: ProviderError | None = None
    for _ in range(retries + 1):
        try:
            return await provider.interpret(request.operator_notes, request.battery, feedback)
        except ProviderError as exc:
            last = exc
    raise last if last is not None else ProviderError("provider call failed")


def build_response(
    request: OptimizeRequest, directives: list[ValidatedDirective]
) -> OptimizeResponse:
    """Optimize, replay, and assemble the response. Raises rather than emit an invalid plan."""
    hours = request.hours_in_order()
    inputs = build_solver_inputs(hours, request.battery, directives)
    solved = solve(inputs)

    plan: list[HourlyPlanEntry] = []
    for hour in range(len(hours)):
        action, magnitude = battery_action_for(solved.battery_net[hour])
        plan.append(
            HourlyPlanEntry(
                hour=hour,
                grid_kwh=solved.grid[hour],
                solar_used_kwh=solved.solar_used[hour],
                battery_action=action,
                battery_kwh=round(magnitude, TOTALS_DECIMALS),
                battery_energy_after_kwh=solved.battery_energy_after[hour],
            )
        )

    total_grid = round(sum(entry.grid_kwh for entry in plan), TOTALS_DECIMALS)
    total_cost = round(
        sum(entry.grid_kwh * hours[index].tariff_bdt_per_kwh for index, entry in enumerate(plan)),
        TOTALS_DECIMALS,
    )
    peak_grid = round(max(entry.grid_kwh for entry in plan), TOTALS_DECIMALS)

    report = replay(
        hours, request.battery, directives, plan, total_grid, total_cost, peak_grid
    )
    if not report.is_valid:
        logger.error("replay rejected the generated plan", extra={"violations": report.violations})
        raise PlanRejected("; ".join(report.violations))

    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=[
            DirectiveInterpretation(
                note_index=directive.note_index,
                applies=directive.applies,
                directive_type=directive.directive_type,
                structured_adjustment=directive.adjustment,
                explanation=directive.explanation,
            )
            for directive in directives
        ],
        hourly_plan=plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary=_summarize(plan, directives, total_cost, peak_grid),
    )


def _summarize(
    plan: list[HourlyPlanEntry],
    directives: list[ValidatedDirective],
    total_cost: float,
    peak_grid: float,
) -> str:
    applied = [d.directive_type for d in directives if d.is_active]
    ignored = sum(1 for d in directives if d.directive_type == "no_op")

    charge_hours = [entry.hour for entry in plan if entry.battery_action == "charge"]
    discharge_hours = [entry.hour for entry in plan if entry.battery_action == "discharge"]

    parts = [
        f"Charged the battery in {len(charge_hours)} low-tariff hours and discharged it in "
        f"{len(discharge_hours)} high-tariff hours, ending at the starting state of charge."
    ]
    if applied:
        parts.append("Applied operator directives: " + ", ".join(sorted(set(applied))) + ".")
    if ignored:
        parts.append(f"Ignored {ignored} note(s) that do not affect today's schedule.")
    parts.append(f"Total grid cost {total_cost:.2f} BDT with a peak import of {peak_grid:.2f} kWh.")
    return " ".join(parts)


async def run(
    chain: list[LLMProvider],
    request: OptimizeRequest,
    repair_attempts: int = 1,
    transport_retries: int = 1,
) -> OptimizeResponse:
    """Interpret, optimize, replay, respond.

    Organizer scoring scenarios are guaranteed feasible, so an infeasible model is evidence that the
    interpretation is wrong rather than a reason to relax a constraint. We therefore re-interpret
    once, telling the model exactly which directives could not be satisfied together, and only then
    give up. A directive is never softened to force a solution.
    """
    directives = await interpret_notes(chain, request, repair_attempts, transport_retries)
    try:
        return build_response(request, directives)
    except InfeasibleScenario as first_failure:
        logger.warning("infeasible under the first interpretation: %s", first_failure)

    feedback = (
        "- the directives you extracted cannot all be satisfied at the same time, so at least one "
        "is misread. Re-read every note. Check the start-inclusive, end-exclusive hour window, "
        "whether a percentage is the fraction REMAINING rather than the size of the cut, and "
        "whether a note is really a directive at all rather than no_op. Your previous answer was: "
        + _describe(directives)
    )
    retried = await interpret_notes(
        chain, request, repair_attempts, transport_retries, initial_feedback=feedback
    )
    return build_response(request, retried)


def _describe(directives: list[ValidatedDirective]) -> str:
    parts = []
    for directive in directives:
        if directive.adjustment is None:
            parts.append(f"note {directive.note_index}: no_op")
        else:
            parts.append(
                f"note {directive.note_index}: {directive.directive_type} {directive.adjustment}"
            )
    return "; ".join(parts)


__all__ = [
    "InfeasibleScenario",
    "InterpretationUnavailable",
    "PlanRejected",
    "SolverFailure",
    "build_response",
    "interpret_notes",
    "run",
]
