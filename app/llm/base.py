"""Language-model provider contract.

Every success path in this service runs through a provider implementing `LLMProvider`. There is no
non-language-model interpreter: the competition requires the model to produce the structured
interpretation that reaches the optimizer, so a deterministic parser may only validate that output,
never substitute for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.schemas import ACTIVE_DIRECTIVE_TYPES, Battery

#: Numeric fields carried in the flat wire format, keyed by the directive type that requires them.
_NUMERIC_FIELDS: dict[str, str] = {
    "solar_reduction": "factor",
    "minimum_battery_reserve": "minimum_energy_kwh",
    "max_grid_window": "max_grid_kwh",
}


class ProviderError(Exception):
    """Transport-level failure: timeout, rate limit, auth problem, or malformed envelope."""


class ProviderNotConfigured(ProviderError):
    """The provider has no credential configured."""


class RateLimited(ProviderError):
    """The provider refused the call with HTTP 429. Carries the server's own wait hint."""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


@dataclass(frozen=True)
class RawInterpretation:
    """Model output before deterministic validation."""

    entries: list[dict[str, Any]]
    provider: str
    model: str


class LLMProvider(Protocol):
    name: str

    async def interpret(
        self,
        notes: list[str],
        battery: Battery,
        repair_feedback: str | None = None,
    ) -> RawInterpretation:
        """Interpret every operator note in a single request."""
        ...


def flat_entries_to_interpretation(flat: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assemble the documented `structured_adjustment` shape from the flat wire format.

    Provider structured-output schemas cannot express a union that varies by directive type, so the
    model answers with a flat record and this function assembles the object required by Problem
    Statement section 04.

    This is shape assembly, not interpretation. No value is altered, clamped, or invented:

    * the directive type the model declared is authoritative,
    * only the keys that type requires are copied across, and a value the model left null is simply
      absent, so the guardrails reject the entry and ask the model to fix it,
    * fields irrelevant to the declared type are dropped as artifacts of the flat schema,
    * a `no_op` that nonetheless names hours is passed through unchanged, so the guardrails can
      reject the contradiction rather than have it papered over here.
    """
    assembled: list[dict[str, Any]] = []
    for record in flat:
        if not isinstance(record, dict):
            assembled.append(record)
            continue

        directive_type = record.get("directive_type")
        entry: dict[str, Any] = {
            "note_index": record.get("note_index"),
            "applies": record.get("applies"),
            "directive_type": directive_type,
            "explanation": record.get("explanation"),
        }

        if directive_type in ACTIVE_DIRECTIVE_TYPES:
            adjustment: dict[str, Any] = {}
            if record.get("hours") is not None:
                adjustment["hours"] = record["hours"]
            numeric_field = _NUMERIC_FIELDS.get(str(directive_type))
            if numeric_field is not None and record.get(numeric_field) is not None:
                adjustment[numeric_field] = record[numeric_field]
            entry["structured_adjustment"] = adjustment
        elif directive_type == "no_op":
            hours = record.get("hours")
            if hours:
                # Contradictory output. Keep it visible for the guardrails.
                entry["structured_adjustment"] = {"hours": hours}
            else:
                entry["structured_adjustment"] = None
        else:
            entry["structured_adjustment"] = record.get("structured_adjustment")

        assembled.append(entry)
    return assembled
