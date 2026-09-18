#!/usr/bin/env python3
"""Post every public sample case at a running service and report the results.

Usage:
    python scripts/run_public_cases.py http://localhost:8000

Semantics, validity, and recalculated cost are compared. The hourly action sequence is NOT
compared: the organizers accept any equivalent optimal schedule.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from pathlib import Path

import httpx

TOLERANCE = 0.01
CASES_FILE = Path(__file__).resolve().parent.parent / "tests" / "data" / "public_cases.json"


def compare_interpretation(expected: list[dict], actual: list[dict]) -> list[str]:
    problems: list[str] = []
    if len(expected) != len(actual):
        return [f"expected {len(expected)} interpretation entries, got {len(actual)}"]
    for index, (want, got) in enumerate(zip(expected, actual)):
        if got.get("note_index") != index:
            problems.append(f"entry {index}: note_index is {got.get('note_index')}")
        for field in ("applies", "directive_type"):
            if want[field] != got.get(field):
                problems.append(
                    f"note {index}: {field} expected {want[field]!r}, got {got.get(field)!r}"
                )
        want_adjustment, got_adjustment = want["structured_adjustment"], got.get(
            "structured_adjustment"
        )
        if want_adjustment is None or got_adjustment is None:
            if want_adjustment != got_adjustment:
                problems.append(f"note {index}: structured_adjustment mismatch")
            continue
        if want_adjustment.get("hours") != got_adjustment.get("hours"):
            problems.append(
                f"note {index}: hours expected {want_adjustment.get('hours')}, "
                f"got {got_adjustment.get('hours')}"
            )
        for key in ("factor", "minimum_energy_kwh", "max_grid_kwh"):
            if key in want_adjustment:
                if abs(float(want_adjustment[key]) - float(got_adjustment.get(key, 1e9))) > TOLERANCE:
                    problems.append(
                        f"note {index}: {key} expected {want_adjustment[key]}, "
                        f"got {got_adjustment.get(key)}"
                    )
    return problems


def main(base_url: str) -> int:
    cases = json.loads(CASES_FILE.read_text())["cases"]
    latencies: list[float] = []
    failures = 0

    with httpx.Client(timeout=35.0) as client:
        health = client.get(f"{base_url.rstrip('/')}/health")
        print(f"/health -> {health.status_code} {health.text.strip()}")

        for case in cases:
            started = time.perf_counter()
            response = client.post(f"{base_url.rstrip('/')}/optimize-energy", json=case["input"])
            elapsed = time.perf_counter() - started
            latencies.append(elapsed)

            if response.status_code != 200:
                failures += 1
                print(f"{case['id']}: HTTP {response.status_code} in {elapsed:.2f}s")
                continue

            body = response.json()
            expected = case["expected_output"]
            problems = compare_interpretation(
                expected["directive_interpretation"], body["directive_interpretation"]
            )
            ours = float(body["total_cost_bdt"])
            theirs = float(expected["total_cost_bdt"])
            ratio = min(1.0, theirs / ours) if ours > TOLERANCE else 1.0

            status = "OK " if not problems and abs(ours - theirs) <= TOLERANCE else "DIFF"
            if status == "DIFF":
                failures += 1
            print(
                f"{case['id']}: {status} cost={ours:.2f} reference={theirs:.2f} "
                f"quality={ratio:.4f} latency={elapsed:.2f}s"
            )
            for problem in problems:
                print(f"    - {problem}")

    if latencies:
        ordered = sorted(latencies)
        p95 = ordered[min(len(ordered) - 1, int(round(0.95 * len(ordered))) - 1)]
        print(
            f"\nlatency: mean={statistics.mean(latencies):.2f}s max={max(latencies):.2f}s "
            f"p95={p95:.2f}s"
        )
    print(f"cases with differences or errors: {failures}/{len(cases)}")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
