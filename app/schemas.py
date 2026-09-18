"""API contract models.

Request and response models are strict (`extra="forbid"`): this is a judging API with a fixed,
documented contract, so an unexpected field is a contract violation rather than forward
compatibility. Field names, types, and ordering follow Problem Statement sections 07 and 10.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

HOURS_IN_DAY = 24

DirectiveType = Literal[
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]

BatteryAction = Literal["charge", "discharge", "idle"]

#: Every directive type that actually changes the optimization model.
ACTIVE_DIRECTIVE_TYPES: frozenset[str] = frozenset(
    {
        "solar_reduction",
        "minimum_battery_reserve",
        "no_charge_window",
        "no_discharge_window",
        "max_grid_window",
    }
)

#: Required `structured_adjustment` keys per directive type (Problem Statement section 04).
REQUIRED_ADJUSTMENT_KEYS: dict[str, frozenset[str]] = {
    "solar_reduction": frozenset({"hours", "factor"}),
    "minimum_battery_reserve": frozenset({"hours", "minimum_energy_kwh"}),
    "no_charge_window": frozenset({"hours"}),
    "no_discharge_window": frozenset({"hours"}),
    "max_grid_window": frozenset({"hours", "max_grid_kwh"}),
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------------------
# Request
# --------------------------------------------------------------------------------------


class HourEntry(StrictModel):
    hour: Annotated[int, Field(ge=0, le=23)]
    demand_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    solar_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    tariff_bdt_per_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Battery(StrictModel):
    capacity_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    initial_energy_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    minimum_energy_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    max_charge_kwh_per_hour: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    max_discharge_kwh_per_hour: Annotated[float, Field(ge=0, allow_inf_nan=False)]

    @model_validator(mode="after")
    def _coherent_battery(self) -> "Battery":
        if self.initial_energy_kwh > self.capacity_kwh:
            raise ValueError("initial_energy_kwh cannot exceed capacity_kwh")
        if self.minimum_energy_kwh > self.capacity_kwh:
            raise ValueError("minimum_energy_kwh cannot exceed capacity_kwh")
        if self.initial_energy_kwh < self.minimum_energy_kwh:
            raise ValueError("initial_energy_kwh cannot be below minimum_energy_kwh")
        return self


class OptimizeRequest(StrictModel):
    scenario_id: Annotated[str, Field(min_length=1)]
    operator_notes: Annotated[list[str], Field(min_length=1, max_length=3)]
    hours: Annotated[list[HourEntry], Field(min_length=HOURS_IN_DAY, max_length=HOURS_IN_DAY)]
    battery: Battery

    @field_validator("operator_notes")
    @classmethod
    def _notes_non_empty(cls, notes: list[str]) -> list[str]:
        for index, note in enumerate(notes):
            if not note or not note.strip():
                raise ValueError(f"operator_notes[{index}] must be a non-empty string")
        return notes

    @field_validator("hours")
    @classmethod
    def _hours_cover_the_day(cls, hours: list[HourEntry]) -> list[HourEntry]:
        seen = [entry.hour for entry in hours]
        if sorted(seen) != list(range(HOURS_IN_DAY)):
            raise ValueError("hours must contain exactly one entry for each hour 0 through 23")
        return hours

    def hours_in_order(self) -> list[HourEntry]:
        """Hour entries sorted by `hour`, independent of the order supplied in the request."""
        return sorted(self.hours, key=lambda entry: entry.hour)


# --------------------------------------------------------------------------------------
# Response
# --------------------------------------------------------------------------------------


class DirectiveInterpretation(StrictModel):
    note_index: int
    applies: bool
    directive_type: DirectiveType
    # Built only from guardrail-validated directives, so the payload shape is guaranteed.
    structured_adjustment: dict[str, Any] | None
    explanation: str


class HourlyPlanEntry(StrictModel):
    hour: Annotated[int, Field(ge=0, le=23)]
    grid_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    solar_used_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    battery_action: BatteryAction
    battery_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]
    battery_energy_after_kwh: Annotated[float, Field(ge=0, allow_inf_nan=False)]


class OptimizeResponse(StrictModel):
    scenario_id: str
    directive_interpretation: list[DirectiveInterpretation]
    hourly_plan: list[HourlyPlanEntry]
    total_grid_kwh: float
    total_cost_bdt: float
    peak_grid_kwh: float
    plan_summary: str


class HealthResponse(StrictModel):
    status: Literal["ok"]


class ErrorResponse(StrictModel):
    """Controlled error body. Never carries secrets, provider payloads, or stack traces."""

    detail: str
