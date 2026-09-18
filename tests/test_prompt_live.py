"""Paraphrase robustness against a real language model.

Marked `live`: needs a configured provider key and network access.

    pytest -m live

Every note here is written by hand for this test. None appears in the public sample pack, because
the hidden cases paraphrase the same rules in unseen wording.
"""

from __future__ import annotations

import httpx
import pytest

from app.config import get_settings
from app.llm.registry import build_chain
from app.pipeline import interpret_notes
from app.schemas import OptimizeRequest

pytestmark = pytest.mark.live

CAPACITY = 400.0

BATTERY = {
    "capacity_kwh": CAPACITY,
    "initial_energy_kwh": 200.0,
    "minimum_energy_kwh": 50.0,
    "max_charge_kwh_per_hour": 100.0,
    "max_discharge_kwh_per_hour": 100.0,
}

HOURS = [
    {"hour": h, "demand_kwh": 150.0, "solar_kwh": 60.0, "tariff_bdt_per_kwh": 8.0}
    for h in range(24)
]

# note, expected directive_type, expected hours, expected numeric field and value
BANK: list[tuple[str, str, list[int] | None, tuple[str, float] | None]] = [
    # --- solar_reduction: "to X" phrasing -------------------------------------------------
    ("PV yield will be about half between 10:00 and 12:00.", "solar_reduction", [10, 11], ("factor", 0.5)),
    ("Output falls to a quarter of forecast from 9 AM to 11 AM.", "solar_reduction", [9, 10], ("factor", 0.25)),
    ("Array output will be roughly one fifth of normal from 1 PM until 4 PM.", "solar_reduction", [13, 14, 15], ("factor", 0.2)),
    # --- solar_reduction: "by X" phrasing (the inversion trap) ----------------------------
    ("Expect generation to drop by 80 percent from 11 AM to 2 PM.", "solar_reduction", [11, 12, 13], ("factor", 0.2)),
    ("Solar will be reduced by three quarters between 2 PM and 4 PM.", "solar_reduction", [14, 15], ("factor", 0.25)),
    ("A 60% cut in rooftop generation is expected from noon until 2 PM.", "solar_reduction", [12, 13], ("factor", 0.4)),
    # --- boundary and clock wording -------------------------------------------------------
    ("Panels are fully shaded from midnight until 3 AM.", "solar_reduction", [0, 1, 2], ("factor", 0.0)),
    ("Charging is unavailable from 12 AM to 2 AM.", "no_charge_window", [0, 1], None),
    ("No charging between noon and 2 PM.", "no_charge_window", [12, 13], None),
    ("Do not charge during the 3 PM hour.", "no_charge_window", [15], None),
    # --- battery windows ------------------------------------------------------------------
    ("The charger is isolated for maintenance from 1 AM until 4 AM.", "no_charge_window", [1, 2, 3], None),
    ("The battery must not supply load from 7 PM until 9 PM during relay testing.", "no_discharge_window", [19, 20], None),
    ("Hold the pack offline for export from 5 PM to 7 PM; it may still charge.", "no_discharge_window", [17, 18], None),
    # --- reserves, absolute and relative --------------------------------------------------
    ("Keep at least 150 kWh stored from 6 PM until 9 PM.", "minimum_battery_reserve", [18, 19, 20], ("minimum_energy_kwh", 150.0)),
    ("Maintain the battery above 25% of capacity from 7 PM to 10 PM.", "minimum_battery_reserve", [19, 20, 21], ("minimum_energy_kwh", 0.25 * CAPACITY)),
    ("The pack should stay at least half full between 6 PM and 8 PM.", "minimum_battery_reserve", [18, 19], ("minimum_energy_kwh", 0.5 * CAPACITY)),
    ("Emergency services need a fifth of the battery held back from 8 PM until 11 PM.", "minimum_battery_reserve", [20, 21, 22], ("minimum_energy_kwh", 0.2 * CAPACITY)),
    # --- grid caps ------------------------------------------------------------------------
    ("Grid import must not exceed 175 kWh in any hour from 6 PM until 9 PM.", "max_grid_window", [18, 19, 20], ("max_grid_kwh", 175.0)),
    ("The substation limits us to 150 kW of import from 7 PM to 10 PM.", "max_grid_window", [19, 20, 21], ("max_grid_kwh", 150.0)),
    ("Keep intake at or below 200 kWh between 5 PM and 7 PM.", "max_grid_window", [17, 18], ("max_grid_kwh", 200.0)),
    # --- distractors, several containing numbers and times --------------------------------
    ("The cafeteria will serve 250 extra meals at 1 PM tomorrow.", "no_op", None, None),
    ("Room 304 moved its 3 PM seminar to next Thursday.", "no_op", None, None),
    ("Library returns are extended by 14 days starting next month.", "no_op", None, None),
    ("Campus WiFi maintenance runs from 2 AM to 4 AM on the guest network.", "no_op", None, None),
    ("The shuttle timetable adds a 7 PM departure from gate 2.", "no_op", None, None),
    ("Registration for the 2026 sports meet closes at 5 PM on the 12th.", "no_op", None, None),
]


async def interpret_one(note: str):
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        chain = build_chain(settings, client)
        if not chain:
            pytest.skip("no language-model provider configured")
        request = OptimizeRequest.model_validate(
            {
                "scenario_id": "LIVE",
                "operator_notes": [note],
                "hours": HOURS,
                "battery": BATTERY,
            }
        )
        return await interpret_notes(chain, request, repair_attempts=settings.llm_repair_attempts)


@pytest.mark.parametrize(
    "note,directive_type,hours,numeric",
    BANK,
    ids=[f"{index:02d}-{entry[1]}" for index, entry in enumerate(BANK)],
)
async def test_paraphrase_is_interpreted_correctly(note, directive_type, hours, numeric):
    result = await interpret_one(note)

    assert len(result) == 1
    directive = result[0]
    assert directive.directive_type == directive_type, f"note: {note}"

    if directive_type == "no_op":
        assert directive.applies is False
        assert directive.adjustment is None
        return

    assert directive.applies is True
    assert directive.adjustment is not None
    assert directive.adjustment["hours"] == hours, f"note: {note}"
    if numeric is not None:
        field, expected = numeric
        assert directive.adjustment[field] == pytest.approx(expected, abs=0.01), f"note: {note}"


async def test_multiple_notes_in_one_request_keep_their_order():
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
        chain = build_chain(settings, client)
        if not chain:
            pytest.skip("no language-model provider configured")
        request = OptimizeRequest.model_validate(
            {
                "scenario_id": "LIVE-MULTI",
                "operator_notes": [
                    "Cloud cover leaves about half the usual solar from 10 AM until noon.",
                    "The charging circuit is unavailable from 2 PM until 4 PM.",
                    "The print shop is closed for stocktaking on the 21st.",
                ],
                "hours": HOURS,
                "battery": BATTERY,
            }
        )
        result = await interpret_notes(chain, request)

    assert [d.note_index for d in result] == [0, 1, 2]
    assert result[0].directive_type == "solar_reduction"
    assert result[0].adjustment == {"hours": [10, 11], "factor": 0.5}
    assert result[1].directive_type == "no_charge_window"
    assert result[1].adjustment == {"hours": [14, 15]}
    assert result[2].directive_type == "no_op"
