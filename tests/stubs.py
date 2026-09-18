"""Stub language-model providers for offline tests."""

from __future__ import annotations

from typing import Any

from app.llm.base import ProviderError, RawInterpretation
from app.schemas import Battery


class StubProvider:
    """Returns a scripted sequence of results, one per call."""

    def __init__(self, name: str, script: list[Any]) -> None:
        self.name = name
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def interpret(
        self, notes: list[str], battery: Battery, repair_feedback: str | None = None
    ) -> RawInterpretation:
        self.calls.append({"notes": notes, "feedback": repair_feedback})
        outcome = self._script.pop(0) if self._script else self._last
        self._last = outcome
        if isinstance(outcome, Exception):
            raise outcome
        return RawInterpretation(entries=outcome, provider=self.name, model="stub")

    _last: Any = None


def solar_entry(note_index: int = 0, hours=(13, 14), factor: float = 0.25):
    return {
        "note_index": note_index,
        "applies": True,
        "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": list(hours), "factor": factor},
        "explanation": "Panel maintenance reduces usable solar.",
    }


def no_op_entry(note_index: int = 1):
    return {
        "note_index": note_index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "Unrelated to today's energy schedule.",
    }


def broken_entry(note_index: int = 0):
    """A factor expressed as a percentage: the classic 'by 80%' mistake."""
    return {
        "note_index": note_index,
        "applies": True,
        "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": [13, 14], "factor": 80},
        "explanation": "Wrong factor scale.",
    }


TRANSPORT_FAILURE = ProviderError("stub transport failure")
