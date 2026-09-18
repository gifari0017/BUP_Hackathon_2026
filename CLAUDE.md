# GridWise — Permanent Competition Rules

This file records the non-negotiable rules for this repository. It is loaded into every session and
must survive context compaction. **When this file and the official Problem Statement disagree, the
Problem Statement wins.**

## Canonical sources

| Document | Canonical for |
|---|---|
| `BUP_CSE_FEST_2026_Preliminary_Problem_Statement_GridWise_LLM.pdf` | API behavior, schemas, operator-note interpretation, directives, guardrails, optimization, battery behavior, energy accounting, validity |
| `BUP_CSE_FEST_2026_Participant_Guide_&_Evaluation_Rubric_GridWise_LLM.pdf` | Scoring, latency, reliability, deployment, Docker, repository policy, documentation, penalties, submission, tie-breaking |
| `BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json` | Validation examples only. Never hard-code its scenario IDs, wording, numbers, or schedules. |

Never modify or delete these three files.

## Hard compliance rules

1. **The LLM is the only interpretation path.** A language model must produce the structured
   `directive_interpretation` that reaches the optimizer. There is no regex interpreter, no heuristic
   interpreter, and no all-`no_op` default anywhere in `app/`. Using an LLM only for `plan_summary` or
   documentation fails the mandatory requirement and forfeits the shortlist.
2. **Guardrails validate; they never repair semantics.** Never clamp a factor, never clamp a reserve,
   never demote an invalid directive to `no_op`, never fabricate a missing entry, never drop a bad
   entry. The only permitted normalization is sorting an otherwise-valid `hours` array and
   value-preserving numeric coercion.
3. **Failure chain:** primary LLM → one bounded repair retry carrying exact validator feedback →
   optional second **language-model** provider → controlled error. Never a fabricated interpretation.
4. **Never soften an official directive.** An infeasible LP means our interpretation or application is
   wrong, or the request is invalid. Diagnose, retry interpretation, else return a controlled error.
   Never return a schedule that knowingly violates an applicable directive.
5. **Never emit a plan that fails the replay validator.** The replay validator is independent of the
   solver and re-derives everything from the request plus validated directives.
6. **No secrets** in the repo, logs, responses, README, Docker images, or Git history. `.env` is
   git-ignored; `.env.example` holds variable names only.
7. **Totals are recomputed from the emitted `hourly_plan`**, never from the LP objective.

## Specification facts that are easy to get wrong

- Time windows are **start-inclusive, end-exclusive**: "1 PM to 3 PM" → `[13, 14]`.
- `solar_reduction.factor` is the fraction of solar **remaining**. An 80% reduction → `factor = 0.2`.
- A percentage battery reserve resolves against `battery.capacity_kwh` (50% of 200 kWh → 100 kWh).
- `noon = 12`, `midnight = 0`.
- Exactly one `directive_interpretation` entry per operator note, in `note_index` order `0..N-1`.
- `no_op` is the **only** directive allowed with `applies = false`, and it must carry
  `structured_adjustment = null`. Every other directive uses `applies = true`.
- `hours` arrays: unique integers 0–23, ascending.
- Energy balance every hour: `grid + solar_used + battery_discharge = demand + battery_charge`.
- End-of-day neutrality: `battery_energy_after_kwh[23] == battery.initial_energy_kwh`.
- `minimum_battery_reserve` raises the floor: `max(base minimum, directive minimum)`.
- Numeric tolerance: 0.01 kWh / 0.01 BDT.
- Irrelevant notes are `no_op` **even when they contain numbers, times, or dates**.

## Documented assumptions (spec is silent)

Two directives of the **same type** covering the same hour. The spec defines each type against a
single directive and no public case exercises an overlap. We apply the most restrictive reading:
`solar_reduction` → `min(factors)`; `minimum_battery_reserve` → `max(values)`; `max_grid_window` →
`min(values)`; `no_charge_window` / `no_discharge_window` → union of hours. Each lives in its own
`compose_*` function in `app/directives.py` so it is a one-line change.

## Architecture invariant

```
request → strict validation → ONE LLM call → strict parse → strict guardrails
        → directive application → LP optimizer → independent replay → recomputed totals → response
```

The LLM never produces the hourly schedule. The optimizer never sees unvalidated model output.

## Operational rules for this repo

- Do not `git push` and do not make the repository public without explicit instruction.
- Do not create, purchase, or enable paid cloud or API services without explicit instruction.
- Public sample cases live only under `tests/`; `app/` must never import them.
