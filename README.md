# GridWise — LLM-Assisted Campus Energy Optimizer

BUP CSE Fest 2026 Hackathon, online preliminary round.

One HTTP service that reads 1–3 natural-language campus operator notes, converts them into
machine-checkable directives with a language model, validates that interpretation deterministically,
applies it to a 24-hour energy scheduling problem, and returns the cost-minimal schedule.

| Endpoint | Purpose |
|---|---|
| `GET /health` | Readiness probe. Never calls a language model. |
| `POST /optimize-energy` | Operator-note interpretation plus the 24-hour optimized schedule. |

---

## 1. Local quickstart from a clean environment

Requires Python 3.11 or newer (developed and tested on 3.13) and network access to your language-
model provider. No database, no build step, no training job.

```bash
# 1. clone and enter the repository
git clone <repository-url>
cd <repository-directory>

# 2. create an isolated environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. configure the language model (see section 2 for how to get a key)
cp .env.example .env
#    edit .env and set GEMINI_API_KEY=<your key>

# 4. start the service
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Verify readiness:

```bash
curl -s http://localhost:8000/health
# {"status":"ok"}
```

Send a scenario:

```bash
curl -s -X POST http://localhost:8000/optimize-energy \
  -H 'content-type: application/json' \
  -d '{
    "scenario_id": "DEMO-1",
    "operator_notes": [
      "Panel washing from 1 PM to 3 PM leaves about a fifth of normal solar output.",
      "The print shop is closed for stocktaking on the 21st."
    ],
    "hours": [
      {"hour": 0,  "demand_kwh": 90,  "solar_kwh": 0,   "tariff_bdt_per_kwh": 6},
      {"hour": 1,  "demand_kwh": 85,  "solar_kwh": 0,   "tariff_bdt_per_kwh": 6},
      {"hour": 2,  "demand_kwh": 80,  "solar_kwh": 0,   "tariff_bdt_per_kwh": 5},
      {"hour": 3,  "demand_kwh": 80,  "solar_kwh": 0,   "tariff_bdt_per_kwh": 5},
      {"hour": 4,  "demand_kwh": 85,  "solar_kwh": 0,   "tariff_bdt_per_kwh": 5},
      {"hour": 5,  "demand_kwh": 95,  "solar_kwh": 0,   "tariff_bdt_per_kwh": 6},
      {"hour": 6,  "demand_kwh": 110, "solar_kwh": 5,   "tariff_bdt_per_kwh": 8},
      {"hour": 7,  "demand_kwh": 130, "solar_kwh": 20,  "tariff_bdt_per_kwh": 10},
      {"hour": 8,  "demand_kwh": 150, "solar_kwh": 50,  "tariff_bdt_per_kwh": 12},
      {"hour": 9,  "demand_kwh": 165, "solar_kwh": 90,  "tariff_bdt_per_kwh": 14},
      {"hour": 10, "demand_kwh": 175, "solar_kwh": 130, "tariff_bdt_per_kwh": 16},
      {"hour": 11, "demand_kwh": 180, "solar_kwh": 160, "tariff_bdt_per_kwh": 16},
      {"hour": 12, "demand_kwh": 185, "solar_kwh": 170, "tariff_bdt_per_kwh": 15},
      {"hour": 13, "demand_kwh": 185, "solar_kwh": 165, "tariff_bdt_per_kwh": 15},
      {"hour": 14, "demand_kwh": 180, "solar_kwh": 150, "tariff_bdt_per_kwh": 14},
      {"hour": 15, "demand_kwh": 175, "solar_kwh": 120, "tariff_bdt_per_kwh": 14},
      {"hour": 16, "demand_kwh": 170, "solar_kwh": 80,  "tariff_bdt_per_kwh": 15},
      {"hour": 17, "demand_kwh": 175, "solar_kwh": 40,  "tariff_bdt_per_kwh": 17},
      {"hour": 18, "demand_kwh": 190, "solar_kwh": 10,  "tariff_bdt_per_kwh": 19},
      {"hour": 19, "demand_kwh": 195, "solar_kwh": 0,   "tariff_bdt_per_kwh": 20},
      {"hour": 20, "demand_kwh": 185, "solar_kwh": 0,   "tariff_bdt_per_kwh": 19},
      {"hour": 21, "demand_kwh": 165, "solar_kwh": 0,   "tariff_bdt_per_kwh": 15},
      {"hour": 22, "demand_kwh": 140, "solar_kwh": 0,   "tariff_bdt_per_kwh": 11},
      {"hour": 23, "demand_kwh": 115, "solar_kwh": 0,   "tariff_bdt_per_kwh": 8}
    ],
    "battery": {
      "capacity_kwh": 200,
      "initial_energy_kwh": 100,
      "minimum_energy_kwh": 40,
      "max_charge_kwh_per_hour": 60,
      "max_discharge_kwh_per_hour": 60
    }
  }'
```

Abbreviated response (`hourly_plan` shortened to its first entry; the real response has all 24):

```json
{
  "scenario_id": "DEMO-1",
  "directive_interpretation": [
    {
      "note_index": 0,
      "applies": true,
      "directive_type": "solar_reduction",
      "structured_adjustment": {"hours": [13, 14], "factor": 0.2},
      "explanation": "Panel washing leaves a fifth of the forecast solar output."
    },
    {
      "note_index": 1,
      "applies": false,
      "directive_type": "no_op",
      "structured_adjustment": null,
      "explanation": "This note does not affect the 24-hour energy schedule."
    }
  ],
  "hourly_plan": [
    {"hour": 0, "grid_kwh": 30.0, "solar_used_kwh": 0.0, "battery_action": "discharge",
     "battery_kwh": 60.0, "battery_energy_after_kwh": 40.0}
  ],
  "total_grid_kwh": 2547.0,
  "total_cost_bdt": 30500.0,
  "peak_grid_kwh": 210.0,
  "plan_summary": "Charged the battery in 7 low-tariff hours and discharged it in 8 high-tariff hours, ending at the starting state of charge. Applied operator directives: solar_reduction. Ignored 1 note(s) that do not affect today's schedule. Total grid cost 30500.00 BDT with a peak import of 210.00 kWh."
}
```

---

## 2. Configuration, model, and provider

All configuration is by environment variable. `.env.example` lists every name; `.env` is
git-ignored and is never committed or copied into an image.

| Variable | Required | Meaning |
|---|---|---|
| `GRIDWISE_LLM_PROVIDER` | yes | Primary provider: `gemini`, `anthropic`, or `openai`. |
| `GRIDWISE_LLM_MODEL` | yes | Model identifier for that provider. |
| `GEMINI_API_KEY` | with `gemini` | Credential for the primary or fallback Gemini provider. |
| `ANTHROPIC_API_KEY` | with `anthropic` | Credential for the Anthropic provider. |
| `OPENAI_API_KEY` | with `openai` | Credential for the OpenAI provider. |
| `GRIDWISE_LLM_FALLBACK_PROVIDER` | no | Second **language-model** provider, tried only if the primary fails. |
| `GRIDWISE_LLM_FALLBACK_MODEL` | no | Model identifier for the fallback provider. |
| `LLM_TIMEOUT_SECONDS` | no | Per-call timeout. Default 12. |
| `LLM_TRANSPORT_RETRIES` | no | Retries for a timeout or rate limit; the server's `Retry-After` is honoured, with exponential backoff otherwise. Default 3. |
| `LLM_REPAIR_ATTEMPTS` | no | Guardrail-feedback repair attempts per provider. Default 1. |
| `PORT` | no | Listen port. Default 8000. |
| `LOG_LEVEL` | no | Default `INFO`. |

**Provider and model used for the submission:** Google Gemini, model `gemini-3.5-flash-lite`, chosen by measurement against this account: it answers the interpretation prompt in about 1.5 s, where the larger Flash models either returned HTTP 503 under load or took over 4 s. The
model identifier is verified against the account before the round rather than assumed:

```bash
python scripts/list_models.py gemini     # prints model ids the key can actually call
```

Getting a Gemini key: create one at <https://aistudio.google.com/apikey> and put it in `.env` as
`GEMINI_API_KEY`. Any of the three providers satisfies the challenge's language-model requirement;
the provider is a configuration choice, not a code change.

---

## 3. Testing against the public sample cases

Offline suite (no key and no network required — 126 tests):

```bash
pip install -r requirements-dev.txt
pytest -m "not live" -q
```

Expected result: **all tests pass.** The suite includes, for each of the ten public sample cases,
that our recalculated `total_cost_bdt` equals the published reference optimal cost within the
official 0.01 tolerance, and that the generated plan passes the independent replay validator.

End-to-end against a running service (needs a configured key):

```bash
uvicorn app.main:app --port 8000 &
python scripts/run_public_cases.py http://localhost:8000
```

The script posts all ten cases, compares the returned interpretation against the reference
semantics, reports the cost-quality ratio `organizer_optimal / ours` for each case, and prints mean,
max, and p95 latency. Expected result: every case `OK`, quality `1.0000`, and
`cases with differences or errors: 0/10`.

Paraphrase robustness against a live model (needs a key; 27 hand-written notes that appear nowhere
in the public pack):

```bash
pytest -m live -q
```

Per the official equivalence note, no test compares the hourly action sequence to the reference
schedule. Equivalent optimal schedules are accepted, so the tests check semantics, validity, and
recalculated cost.

---

## 4. Architecture: language model, guardrails, optimizer

```
HTTP request
  → strict request validation (Pydantic, unknown fields rejected)
  → ONE language-model call interpreting all 1–3 operator notes
  → strict structured-output parsing
  → deterministic guardrail validation  ── invalid ──► one repair retry with validator feedback
  → deterministic directive application            ──► then a second language-model provider
  → linear-programming optimizer (HiGHS)           ──► then a controlled error
  → independent hour-by-hour replay validator
  → totals recomputed from the emitted plan
  → response
```

**The language model does the language work only.** It converts notes into the six supported
directive types and never produces the hourly schedule. Because a provider schema cannot express an
adjustment whose shape depends on the directive type, the model answers in a flat structured format
and `app/llm/base.py` assembles the documented `structured_adjustment` object without altering any
value.

**Guardrails validate; they never repair meaning** (`app/guardrails.py`). Nothing is clamped,
demoted to `no_op`, dropped, or fabricated. A `factor` of `80` is rejected with an explanation, not
silently turned into `1.0`; a missing note entry is rejected, not invented as `no_op`. The only
normalizations are representational: sorting an otherwise-valid `hours` array, widening an integer
to a float, and supplying neutral `explanation` text when the model omitted it. Rejections become
feedback for one bounded repair attempt, then the second provider is tried.

**There is no non-model interpreter.** The challenge requires a language model in the
operator-note interpretation path, so no regex or heuristic fallback exists in `app/` — a test
asserts the absence. If every configured provider fails, the service returns a controlled error
rather than a fabricated interpretation.

**The optimizer is exact** (`app/optimizer.py`), not a greedy heuristic. Per hour it solves for
`s[h]`, the solar energy used, and `b[h]`, the *signed* net battery flow, with

```
minimize   Σ tariff[h] · grid[h]           grid[h] = demand[h] − s[h] + b[h]
subject to grid[h] ≥ 0,  grid[h] ≤ cap[h]  (max_grid_window)
           0 ≤ s[h] ≤ effective_solar[h]   (solar_reduction)
           −max_discharge ≤ b[h] ≤ max_charge, tightened to b[h] ≤ 0 in a no-charge window
                                             and b[h] ≥ 0 in a no-discharge window
           floor[h] ≤ E0 + Σ_{k≤h} b[k] ≤ capacity   (minimum_battery_reserve raises floor[h])
           Σ b[h] = 0                                (end-of-day neutrality)
```

solved with `scipy.optimize.linprog(method="highs")`. A single signed battery variable removes the
degeneracy in which a lossless battery would charge and discharge in the same hour, which would
leave `battery_action` ambiguous. **No constraint is ever softened**: an infeasible model returns a
controlled 422, because an applicable operator directive is a hard operational rule.

**The replay validator is independent** (`app/replay.py`). It does not reuse the solver's model. It
re-derives effective solar, battery state, and every bound from the request plus the validated
directives, checks each directive directly against `hourly_plan` the way the judge does, and
verifies the reported totals against the emitted plan. A plan that fails is never returned.

Deterministic path cost, measured over 200 runs on the public cases: mean 1.6 ms, p95 2.6 ms. The
single language-model call dominates request latency.

---

## 5. Docker fallback

The image contains no credentials. Supply them at runtime.

```bash
docker pull ghcr.io/<owner>/<repository>:<tag>

docker run --rm -p 8000:8000 \
  -e GRIDWISE_LLM_PROVIDER=gemini \
  -e GRIDWISE_LLM_MODEL=<model id> \
  -e GEMINI_API_KEY=<your key> \
  ghcr.io/<owner>/<repository>:<tag>

curl -s http://localhost:8000/health
# {"status":"ok"}
```

The container binds `0.0.0.0`, honours `PORT` (default 8000), runs as a non-root user, and carries a
`HEALTHCHECK`. Build locally with `docker build -t gridwise:local .`. The GitHub Actions workflow in
`.github/workflows/docker.yml` builds the image, smoke-tests `/health` with no credentials present,
and pushes it to GHCR with both a `latest` and an immutable commit-SHA tag.

---

## 6. Dependencies, limitations, and secret handling

**Dependencies** (`requirements.txt`): FastAPI and Uvicorn for the HTTP service, Pydantic and
pydantic-settings for the strict schema and configuration, SciPy (HiGHS) and NumPy for the linear
program, and httpx for provider calls. Development adds pytest and pytest-asyncio. All are
open-source libraries used as published; the architecture, prompt, guardrails, optimizer model, and
replay validator are the team's own work. Claude Code was used as an AI coding assistant, which the
official rules permit.

**Secret handling.** Keys are read from the environment only. `.env` and `.env.*` are git-ignored
(`.env.example` holds names with empty values). No key is logged: a missing credential is reported
by *variable name*. Error responses are generic (`{"detail": "..."}`) and never contain provider
payloads, prompts, or stack traces. The Docker image contains no credentials, and CI fails the build
if a credential-shaped string appears in the tree.

**Known limitations.**

1. *Same-type overlapping directives.* The Problem Statement defines each directive's effect against
   a single directive and does not specify what happens when two directives of the **same** type
   cover the same hour; no public case exercises it. We apply the most restrictive reading —
   `solar_reduction` takes the smaller factor, `minimum_battery_reserve` the larger reserve,
   `max_grid_window` the smaller cap, and the charge/discharge windows take the union of hours. Each
   rule is isolated in a `compose_*` function in `app/directives.py`. Cross-type overlap is fully
   specified and needs no assumption.
2. *Strict request validation.* Unknown top-level or nested fields are rejected with 422, since this
   is a fixed documented contract rather than an evolving public API.
3. *No caching.* Every request makes its own language-model call. A cache keyed on anything less
   than the complete interpretation input could serve a wrong interpretation, which is far worse
   than one extra call.
4. *Provider availability.* If every configured language-model provider fails, the request returns a
   controlled 500. Nothing fabricates an interpretation, because the language model is a mandatory
   part of the interpretation path. Configuring a second provider reduces this exposure.
5. *Request timeout budget.* A repair retry costs a second model call. `LLM_TIMEOUT_SECONDS`,
   `LLM_TRANSPORT_RETRIES`, and `LLM_REPAIR_ATTEMPTS` bound the worst case well inside the 30-second
   per-request limit.
