"""Deterministic semantic validation of language-model output.

This module **validates**. It never repairs semantics.

Forbidden here, by competition rule: clamping a factor or a reserve, demoting an invalid directive
to `no_op`, fabricating an entry for a missing note, or dropping a malformed entry. Any of those
would silently change the meaning of the model's answer and hide an interpretation error from the
judge.

The only permitted normalizations are representational and provably meaning-preserving:

* sorting an otherwise-valid `hours` array into ascending order,
* widening an integer to a float for a numeric field,
* supplying neutral `explanation` text when the model omitted it (free-text wording is explicitly
  not judged byte-for-byte, so this carries no semantic content).

Validation failures are returned as human-readable messages that are fed straight back to the model
as repair feedback, so message wording is part of the interpretation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.schemas import (
    ACTIVE_DIRECTIVE_TYPES,
    HOURS_IN_DAY,
    REQUIRED_ADJUSTMENT_KEYS,
    Battery,
)

ALLOWED_DIRECTIVE_TYPES: frozenset[str] = ACTIVE_DIRECTIVE_TYPES | {"no_op"}


class InterpretationInvalid(Exception):
    """Raised when model output cannot be accepted as-is. Carries repair feedback."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class ValidatedDirective:
    """One guardrail-approved interpretation entry."""

    note_index: int
    applies: bool
    directive_type: str
    adjustment: dict[str, Any] | None
    explanation: str

    @property
    def is_active(self) -> bool:
        return self.directive_type in ACTIVE_DIRECTIVE_TYPES


def _is_real_number(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float("inf"), float("-inf"))


def _is_integer(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    return isinstance(value, float) and value.is_integer()


def _validate_hours(raw_hours: Any, label: str, errors: list[str]) -> list[int] | None:
    if not isinstance(raw_hours, list) or not raw_hours:
        errors.append(f"{label}: 'hours' must be a non-empty array of integers from 0 to 23")
        return None

    hours: list[int] = []
    for item in raw_hours:
        if not _is_integer(item):
            errors.append(f"{label}: 'hours' contains {item!r}, which is not an integer")
            return None
        hour = int(item)
        if not 0 <= hour <= HOURS_IN_DAY - 1:
            errors.append(f"{label}: hour {hour} is outside the allowed range 0 to 23")
            return None
        hours.append(hour)

    if len(set(hours)) != len(hours):
        errors.append(f"{label}: 'hours' contains duplicate values {sorted(hours)}")
        return None

    # Sorting is representational only: the set of affected hours is unchanged.
    return sorted(hours)


def _validate_adjustment(
    directive_type: str,
    adjustment: Any,
    battery: Battery,
    label: str,
    errors: list[str],
) -> dict[str, Any] | None:
    if not isinstance(adjustment, dict):
        errors.append(
            f"{label}: directive_type '{directive_type}' requires a structured_adjustment object"
        )
        return None

    required = REQUIRED_ADJUSTMENT_KEYS[directive_type]
    present = set(adjustment)
    missing = required - present
    unexpected = present - required
    if missing:
        errors.append(
            f"{label}: structured_adjustment is missing required key(s) {sorted(missing)} "
            f"for directive_type '{directive_type}'"
        )
        return None
    if unexpected:
        errors.append(
            f"{label}: structured_adjustment has unsupported key(s) {sorted(unexpected)} "
            f"for directive_type '{directive_type}'"
        )
        return None

    hours = _validate_hours(adjustment.get("hours"), label, errors)
    if hours is None:
        return None

    clean: dict[str, Any] = {"hours": hours}

    if directive_type == "solar_reduction":
        factor = adjustment["factor"]
        if not _is_real_number(factor):
            errors.append(f"{label}: 'factor' must be a finite number")
            return None
        if not 0.0 <= float(factor) <= 1.0:
            errors.append(
                f"{label}: 'factor' is {factor}, which is outside the allowed range 0 to 1. "
                "'factor' is the fraction of solar output that REMAINS usable, so an 80% "
                "reduction is 0.2, not 80 and not 0.8"
            )
            return None
        clean["factor"] = float(factor)

    elif directive_type == "minimum_battery_reserve":
        reserve = adjustment["minimum_energy_kwh"]
        if not _is_real_number(reserve):
            errors.append(f"{label}: 'minimum_energy_kwh' must be a finite number")
            return None
        if float(reserve) < 0:
            errors.append(f"{label}: 'minimum_energy_kwh' is {reserve}, which is negative")
            return None
        if float(reserve) > battery.capacity_kwh:
            errors.append(
                f"{label}: 'minimum_energy_kwh' is {reserve}, which exceeds the battery capacity "
                f"of {battery.capacity_kwh} kWh. A percentage reserve must be converted against "
                "that capacity"
            )
            return None
        clean["minimum_energy_kwh"] = float(reserve)

    elif directive_type == "max_grid_window":
        cap = adjustment["max_grid_kwh"]
        if not _is_real_number(cap):
            errors.append(f"{label}: 'max_grid_kwh' must be a finite number")
            return None
        if float(cap) < 0:
            errors.append(f"{label}: 'max_grid_kwh' is {cap}, which is negative")
            return None
        clean["max_grid_kwh"] = float(cap)

    return clean


def _default_explanation(directive_type: str, adjustment: dict[str, Any] | None) -> str:
    if directive_type == "no_op" or adjustment is None:
        return "This note does not affect the 24-hour energy schedule."
    hours = adjustment["hours"]
    window = f"hours {hours[0]}-{hours[-1]}" if len(hours) > 1 else f"hour {hours[0]}"
    return f"Applied {directive_type} to {window}."


def validate_interpretation(
    raw_entries: Any,
    notes: list[str],
    battery: Battery,
) -> list[ValidatedDirective]:
    """Validate raw model output against the Problem Statement guardrails.

    Returns the validated directives on success. Raises `InterpretationInvalid` carrying precise
    feedback otherwise. Nothing is clamped, demoted, dropped, or fabricated.
    """
    errors: list[str] = []

    if not isinstance(raw_entries, list):
        raise InterpretationInvalid(
            ["the interpretation must be an array with exactly one entry per operator note"]
        )

    if len(raw_entries) != len(notes):
        raise InterpretationInvalid(
            [
                f"the interpretation has {len(raw_entries)} entries but there are {len(notes)} "
                f"operator notes; return exactly one entry per note, with note_index "
                f"0 through {len(notes) - 1}"
            ]
        )

    by_index: dict[int, dict[str, Any]] = {}
    for position, entry in enumerate(raw_entries):
        if not isinstance(entry, dict):
            errors.append(f"entry at position {position} is not an object")
            continue
        note_index = entry.get("note_index")
        if not _is_integer(note_index):
            errors.append(f"entry at position {position} has a non-integer note_index")
            continue
        note_index = int(note_index)
        if not 0 <= note_index < len(notes):
            errors.append(
                f"entry at position {position} has note_index {note_index}, which does not "
                f"identify an operator note (valid range 0 to {len(notes) - 1})"
            )
            continue
        if note_index in by_index:
            errors.append(f"note_index {note_index} appears more than once")
            continue
        by_index[note_index] = entry

    for note_index in range(len(notes)):
        if note_index not in by_index:
            errors.append(f"note_index {note_index} is missing from the interpretation")

    if errors:
        raise InterpretationInvalid(errors)

    validated: list[ValidatedDirective] = []
    for note_index in range(len(notes)):
        entry = by_index[note_index]
        label = f"note_index {note_index}"

        directive_type = entry.get("directive_type")
        if directive_type not in ALLOWED_DIRECTIVE_TYPES:
            errors.append(
                f"{label}: directive_type {directive_type!r} is not supported. Use one of "
                f"{sorted(ALLOWED_DIRECTIVE_TYPES)}"
            )
            continue

        applies = entry.get("applies")
        if not isinstance(applies, bool):
            errors.append(f"{label}: 'applies' must be true or false")
            continue

        adjustment_raw = entry.get("structured_adjustment", None)

        if directive_type == "no_op":
            if applies is not False:
                errors.append(f"{label}: directive_type 'no_op' requires applies = false")
                continue
            if adjustment_raw is not None:
                errors.append(
                    f"{label}: directive_type 'no_op' requires structured_adjustment = null"
                )
                continue
            adjustment: dict[str, Any] | None = None
        else:
            if applies is not True:
                errors.append(
                    f"{label}: directive_type '{directive_type}' requires applies = true. "
                    "Only 'no_op' may use applies = false"
                )
                continue
            adjustment = _validate_adjustment(
                directive_type, adjustment_raw, battery, label, errors
            )
            if adjustment is None:
                continue

        explanation = entry.get("explanation")
        if not isinstance(explanation, str) or not explanation.strip():
            explanation = _default_explanation(directive_type, adjustment)

        validated.append(
            ValidatedDirective(
                note_index=note_index,
                applies=applies,
                directive_type=directive_type,
                adjustment=adjustment,
                explanation=explanation.strip(),
            )
        )

    if errors:
        raise InterpretationInvalid(errors)

    return validated
