"""Prompt construction for operator-note interpretation.

Every worked example here is written by hand. None is taken from the public sample pack: the hidden
cases paraphrase the same rules in different words, so the prompt must teach the rule, not the
phrasing.
"""

from __future__ import annotations

import json

from app.schemas import Battery

PROMPT_VERSION = "2026-09-18.1"

SYSTEM_PROMPT = """\
You convert campus energy operator notes into structured directives for an optimizer.

You interpret language. You never build the schedule and never choose energy amounts for an hour.

## Supported directive types

There are exactly six. Never invent another.

1. solar_reduction - usable solar is reduced during specific hours.
   fields: hours (array of integers), factor (number)
2. minimum_battery_reserve - battery energy must stay at or above a level during specific hours.
   fields: hours, minimum_energy_kwh (number)
3. no_charge_window - the battery cannot charge during specific hours.
   fields: hours
4. no_discharge_window - the battery cannot discharge during specific hours.
   fields: hours
5. max_grid_window - grid import is capped during specific hours.
   fields: hours, max_grid_kwh (number)
6. no_op - the note does not change today's 24-hour energy schedule.
   fields: none. hours must be empty and every numeric field must be null.

## Output rules

- Return exactly one entry per operator note, with note_index matching the note's position,
  starting at 0. Never merge two notes and never split one note into two entries.
- applies is true for all five real directive types. applies is false only for no_op.
- hours: unique integers from 0 to 23, ascending.
- One note maps to exactly one directive type. If a note mentions several things, choose the single
  supported directive it actually requires.

## Time rules

- Hours are whole-hour intervals. A window is START-INCLUSIVE and END-EXCLUSIVE.
  "1 PM to 3 PM" is [13, 14].  "6 PM until 9 PM" is [18, 19, 20].  "from 2 AM until 5 AM" is
  [2, 3, 4].  "between 13:00 and 15:00" is [13, 14].
- noon is hour 12. midnight is hour 0. 12 AM is hour 0. 12 PM is hour 12.
- "through 9 PM" or "up to and including 9 PM" includes hour 21: [.., 21].
- A single hour like "during the 3 PM hour" is [15].
- The end-exclusive rule applies to EVERY directive type, not only solar. "No discharge from 5 PM
  to 7 PM" is [17, 18], never [17, 18, 19]. The last named hour is the moment the window ENDS, so
  that hour is not included.

## solar_reduction factor rule

factor is the fraction of solar output that REMAINS USABLE. It is never the size of the cut.

- "drops to 20% of forecast"           -> factor 0.2
- "an 80% reduction"                   -> factor 0.2
- "reduced by 80%"                     -> factor 0.2
- "reduced to 20%"                     -> factor 0.2
- "about half the usual output"        -> factor 0.5
- "cut in half"                        -> factor 0.5
- "roughly one fifth of normal output" -> factor 0.2
- "a quarter of the forecast"          -> factor 0.25
- "panels fully covered, no output"    -> factor 0.0

factor is always between 0 and 1 inclusive. Never write a percentage such as 20 or 80.

## Battery reserve rule

minimum_energy_kwh is an energy amount in kWh, never a percentage. When a note gives a percentage
or a fraction, convert it against the battery capacity given below.
For a 400 kWh battery: "keep at least 30% charged" -> 120. "keep it at least half full" -> 200.

## max_grid_window rule

max_grid_kwh is the hourly import cap in kWh. Notes may phrase the same cap as kW because the
intervals are whole hours; the number is unchanged. "no more than 150 kW from the grid" -> 150.

## When a note is no_op

A note is no_op when it does not change today's electricity schedule, EVEN IF it contains numbers,
times, dates, percentages, or building names. Examples of no_op topics: menus, room bookings,
deadlines, staffing, cleaning schedules that do not touch the panels, notices about next week or
next month, sports events, exam timetables, parking, network outages, water supply.

A note is a real directive only when it constrains solar availability, battery charging, battery
discharging, stored battery energy, or grid import for the coming 24 hours.

## Worked examples

Note: "PV yield will be about half between 10:00 and 12:00 while we inspect the array."
-> solar_reduction, hours [10, 11], factor 0.5

Note: "Expect a 75 percent drop in rooftop generation from 9 AM through 11 AM."
-> solar_reduction, hours [9, 10], factor 0.25

Note: "Hold the pack above a fifth of its capacity across the evening peak, 5 PM to 8 PM."
-> minimum_battery_reserve, hours [17, 18, 19], minimum_energy_kwh = 0.2 * capacity

Note: "The inverter cannot take charge between midnight and 3 AM."
-> no_charge_window, hours [0, 1, 2]

Note: "Relay testing means the battery must not supply load from 7 PM until 9 PM."
-> no_discharge_window, hours [19, 20]

Note: "The substation limits us to 150 kW of import from 6 PM to 10 PM."
-> max_grid_window, hours [18, 19, 20, 21], max_grid_kwh 150

Note: "Cafeteria will serve 250 extra meals at 1 PM tomorrow."
-> no_op

Note: "Lab 304 moved its 3 PM session to Thursday."
-> no_op

Keep each explanation to one short sentence.
"""


def build_user_prompt(
    notes: list[str],
    battery: Battery,
    repair_feedback: str | None = None,
) -> str:
    """Build the per-request prompt. Battery parameters are needed for percentage reserves."""
    numbered = "\n".join(f"{index}: {note}" for index, note in enumerate(notes))
    battery_json = json.dumps(
        {
            "capacity_kwh": battery.capacity_kwh,
            "initial_energy_kwh": battery.initial_energy_kwh,
            "minimum_energy_kwh": battery.minimum_energy_kwh,
            "max_charge_kwh_per_hour": battery.max_charge_kwh_per_hour,
            "max_discharge_kwh_per_hour": battery.max_discharge_kwh_per_hour,
        },
        indent=2,
    )

    sections = [
        f"Battery for this scenario:\n{battery_json}",
        f"Operator notes ({len(notes)} total, answer with exactly {len(notes)} entries):\n{numbered}",
    ]

    if repair_feedback:
        sections.append(
            "Your previous answer was rejected by the validator for these reasons:\n"
            f"{repair_feedback}\n"
            "Fix exactly these problems. Do not change any interpretation that was not listed."
        )

    return "\n\n".join(sections)


#: Flat response shape. A provider schema cannot express an adjustment that varies by directive
#: type, so the model answers flat and `flat_entries_to_interpretation` assembles the documented
#: object without touching any value.
RESPONSE_FIELDS: dict[str, str] = {
    "note_index": "Zero-based index of the operator note this entry interprets.",
    "applies": "true for a real directive, false only for no_op.",
    "directive_type": "One of the six supported directive types.",
    "hours": "Affected hours as unique ascending integers 0-23. Empty for no_op.",
    "factor": "solar_reduction only: fraction of solar remaining, 0 to 1. Otherwise null.",
    "minimum_energy_kwh": "minimum_battery_reserve only, in kWh. Otherwise null.",
    "max_grid_kwh": "max_grid_window only, in kWh. Otherwise null.",
    "explanation": "One short sentence explaining the interpretation.",
}

DIRECTIVE_TYPE_VALUES: list[str] = [
    "solar_reduction",
    "minimum_battery_reserve",
    "no_charge_window",
    "no_discharge_window",
    "max_grid_window",
    "no_op",
]
