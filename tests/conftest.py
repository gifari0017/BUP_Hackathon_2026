from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.guardrails import ValidatedDirective
from app.schemas import OptimizeRequest

DATA = Path(__file__).parent / "data" / "public_cases.json"


def load_cases() -> list[dict[str, Any]]:
    return json.loads(DATA.read_text())["cases"]


@pytest.fixture(scope="session")
def public_cases() -> list[dict[str, Any]]:
    return load_cases()


def directives_from_expected(expected: list[dict[str, Any]]) -> list[ValidatedDirective]:
    """Turn a public case's reference interpretation into guardrail-shaped directives."""
    return [
        ValidatedDirective(
            note_index=entry["note_index"],
            applies=entry["applies"],
            directive_type=entry["directive_type"],
            adjustment=entry["structured_adjustment"],
            explanation=entry.get("explanation", ""),
        )
        for entry in expected
    ]


def request_from_case(case: dict[str, Any]) -> OptimizeRequest:
    return OptimizeRequest.model_validate(case["input"])
