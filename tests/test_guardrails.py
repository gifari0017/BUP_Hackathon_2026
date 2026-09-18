"""Guardrails must REJECT bad model output, never quietly repair its meaning."""

from __future__ import annotations

import pytest

from app.guardrails import InterpretationInvalid, validate_interpretation
from app.schemas import Battery

BATTERY = Battery(
    capacity_kwh=200.0,
    initial_energy_kwh=100.0,
    minimum_energy_kwh=40.0,
    max_charge_kwh_per_hour=50.0,
    max_discharge_kwh_per_hour=50.0,
)
ONE_NOTE = ["Solar drops to a quarter from 1 PM to 3 PM."]
TWO_NOTES = ONE_NOTE + ["The cafeteria menu changes tomorrow."]


def entry(**overrides):
    base = {
        "note_index": 0,
        "applies": True,
        "directive_type": "solar_reduction",
        "structured_adjustment": {"hours": [13, 14], "factor": 0.25},
        "explanation": "Panel washing.",
    }
    base.update(overrides)
    return base


def no_op(index: int = 1):
    return {
        "note_index": index,
        "applies": False,
        "directive_type": "no_op",
        "structured_adjustment": None,
        "explanation": "Not an energy instruction.",
    }


# --------------------------------------------------------------------------- happy path


def test_accepts_a_valid_interpretation():
    result = validate_interpretation([entry(), no_op()], TWO_NOTES, BATTERY)

    assert [d.note_index for d in result] == [0, 1]
    assert result[0].adjustment == {"hours": [13, 14], "factor": 0.25}
    assert result[1].adjustment is None


def test_sorts_an_otherwise_valid_hours_array():
    """The one permitted normalization: order is representational, the hour set is unchanged."""
    result = validate_interpretation(
        [entry(structured_adjustment={"hours": [14, 13], "factor": 0.25})], ONE_NOTE, BATTERY
    )

    assert result[0].adjustment["hours"] == [13, 14]


def test_entries_are_returned_in_note_index_order():
    result = validate_interpretation(
        [no_op(1), entry(note_index=0)], TWO_NOTES, BATTERY
    )

    assert [d.note_index for d in result] == [0, 1]


def test_supplies_neutral_text_when_explanation_is_missing():
    result = validate_interpretation([entry(explanation="")], ONE_NOTE, BATTERY)

    assert result[0].explanation
    assert result[0].directive_type == "solar_reduction"


# --------------------------------------------------------------------------- rejections


def test_factor_out_of_range_is_rejected_not_clamped():
    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation(
            [entry(structured_adjustment={"hours": [13], "factor": 80})], ONE_NOTE, BATTERY
        )

    assert "outside the allowed range 0 to 1" in str(excinfo.value)
    assert "REMAINS" in str(excinfo.value)


def test_missing_note_is_rejected_not_turned_into_no_op():
    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation([entry()], TWO_NOTES, BATTERY)

    assert "exactly one entry per note" in str(excinfo.value)


def test_reserve_above_capacity_is_rejected_not_clamped():
    bad = entry(
        directive_type="minimum_battery_reserve",
        structured_adjustment={"hours": [18], "minimum_energy_kwh": 5000},
    )

    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation([bad], ONE_NOTE, BATTERY)

    assert "exceeds the battery capacity" in str(excinfo.value)


def test_unsupported_directive_type_is_rejected_not_demoted():
    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation([entry(directive_type="load_shift")], ONE_NOTE, BATTERY)

    assert "not supported" in str(excinfo.value)


@pytest.mark.parametrize(
    "hours",
    [[25], [-1], [13, 13], [1.5], ["13"], [], [True]],
    ids=["too-large", "negative", "duplicate", "fractional", "string", "empty", "boolean"],
)
def test_invalid_hours_are_rejected(hours):
    with pytest.raises(InterpretationInvalid):
        validate_interpretation(
            [entry(structured_adjustment={"hours": hours, "factor": 0.5})], ONE_NOTE, BATTERY
        )


def test_duplicate_note_index_is_rejected():
    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation([entry(), entry()], TWO_NOTES, BATTERY)

    assert "more than once" in str(excinfo.value)


def test_applies_false_on_a_real_directive_is_rejected():
    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation([entry(applies=False)], ONE_NOTE, BATTERY)

    assert "requires applies = true" in str(excinfo.value)


def test_no_op_with_an_adjustment_is_rejected():
    bad = no_op(0)
    bad["structured_adjustment"] = {"hours": [3]}

    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation([bad], ONE_NOTE, BATTERY)

    assert "structured_adjustment = null" in str(excinfo.value)


def test_no_op_with_applies_true_is_rejected():
    bad = no_op(0)
    bad["applies"] = True

    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation([bad], ONE_NOTE, BATTERY)

    assert "applies = false" in str(excinfo.value)


def test_missing_required_adjustment_key_is_rejected():
    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation(
            [entry(structured_adjustment={"hours": [13]})], ONE_NOTE, BATTERY
        )

    assert "missing required key" in str(excinfo.value)


def test_unsupported_adjustment_key_is_rejected():
    with pytest.raises(InterpretationInvalid) as excinfo:
        validate_interpretation(
            [entry(structured_adjustment={"hours": [13], "factor": 0.2, "note": "x"})],
            ONE_NOTE,
            BATTERY,
        )

    assert "unsupported key" in str(excinfo.value)


def test_negative_grid_cap_is_rejected():
    bad = entry(
        directive_type="max_grid_window",
        structured_adjustment={"hours": [19], "max_grid_kwh": -5},
    )

    with pytest.raises(InterpretationInvalid):
        validate_interpretation([bad], ONE_NOTE, BATTERY)


def test_non_list_output_is_rejected():
    with pytest.raises(InterpretationInvalid):
        validate_interpretation({"interpretation": []}, ONE_NOTE, BATTERY)
