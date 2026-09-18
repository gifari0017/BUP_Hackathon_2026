"""Request/response model behavior and wire-format assembly."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.llm.base import flat_entries_to_interpretation
from app.schemas import Battery, HourEntry, OptimizeRequest


def valid_hours():
    return [
        {"hour": h, "demand_kwh": 100.0, "solar_kwh": 0.0, "tariff_bdt_per_kwh": 5.0}
        for h in range(24)
    ]


def battery_dict(**overrides):
    base = {
        "capacity_kwh": 200.0,
        "initial_energy_kwh": 100.0,
        "minimum_energy_kwh": 40.0,
        "max_charge_kwh_per_hour": 50.0,
        "max_discharge_kwh_per_hour": 50.0,
    }
    base.update(overrides)
    return base


def test_accepts_a_well_formed_request():
    request = OptimizeRequest.model_validate(
        {
            "scenario_id": "X",
            "operator_notes": ["a note"],
            "hours": valid_hours(),
            "battery": battery_dict(),
        }
    )

    assert len(request.hours_in_order()) == 24


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        OptimizeRequest.model_validate(
            {
                "scenario_id": "X",
                "operator_notes": ["a"],
                "hours": valid_hours(),
                "battery": battery_dict(),
                "extra": 1,
            }
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"initial_energy_kwh": 500.0},
        {"minimum_energy_kwh": 500.0},
        {"initial_energy_kwh": 10.0},
    ],
    ids=["initial-above-capacity", "minimum-above-capacity", "initial-below-minimum"],
)
def test_rejects_incoherent_battery(overrides):
    with pytest.raises(ValidationError):
        Battery.model_validate(battery_dict(**overrides))


def test_rejects_non_finite_numbers():
    with pytest.raises(ValidationError):
        HourEntry.model_validate(
            {"hour": 0, "demand_kwh": float("inf"), "solar_kwh": 0.0, "tariff_bdt_per_kwh": 1.0}
        )


# --------------------------------------------------------------------- flat wire format


def test_flat_conversion_builds_the_documented_adjustment_shape():
    flat = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "solar_reduction",
            "hours": [13, 14],
            "factor": 0.2,
            "minimum_energy_kwh": None,
            "max_grid_kwh": None,
            "explanation": "x",
        }
    ]

    assembled = flat_entries_to_interpretation(flat)

    assert assembled[0]["structured_adjustment"] == {"hours": [13, 14], "factor": 0.2}


def test_flat_conversion_drops_fields_irrelevant_to_the_declared_type():
    flat = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "no_charge_window",
            "hours": [2, 3],
            "factor": 0.5,
            "minimum_energy_kwh": 10.0,
            "max_grid_kwh": 99.0,
            "explanation": "x",
        }
    ]

    assembled = flat_entries_to_interpretation(flat)

    assert assembled[0]["structured_adjustment"] == {"hours": [2, 3]}


def test_flat_conversion_leaves_a_missing_required_value_absent():
    """A null numeric must surface as a guardrail failure, never as an invented default."""
    flat = [
        {
            "note_index": 0,
            "applies": True,
            "directive_type": "max_grid_window",
            "hours": [19],
            "factor": None,
            "minimum_energy_kwh": None,
            "max_grid_kwh": None,
            "explanation": "x",
        }
    ]

    assembled = flat_entries_to_interpretation(flat)

    assert "max_grid_kwh" not in assembled[0]["structured_adjustment"]


def test_flat_conversion_keeps_a_contradictory_no_op_visible():
    flat = [
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "hours": [5],
            "factor": None,
            "minimum_energy_kwh": None,
            "max_grid_kwh": None,
            "explanation": "x",
        }
    ]

    assembled = flat_entries_to_interpretation(flat)

    assert assembled[0]["structured_adjustment"] == {"hours": [5]}


def test_flat_conversion_maps_a_clean_no_op_to_null():
    flat = [
        {
            "note_index": 0,
            "applies": False,
            "directive_type": "no_op",
            "hours": [],
            "factor": None,
            "minimum_energy_kwh": None,
            "max_grid_kwh": None,
            "explanation": "x",
        }
    ]

    assembled = flat_entries_to_interpretation(flat)

    assert assembled[0]["structured_adjustment"] is None
