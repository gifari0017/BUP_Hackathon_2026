#!/usr/bin/env python3
"""List the models the configured credential can actually reach.

Run this before pinning GRIDWISE_LLM_MODEL so the service never depends on a model identifier that
merely looked plausible. Reads the key from the environment and never prints it.

Usage:
    python scripts/list_models.py [gemini|anthropic|openai]
"""

from __future__ import annotations

import os
import sys

import httpx


def gemini() -> None:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        raise SystemExit("GEMINI_API_KEY is not set")
    response = httpx.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        headers={"x-goog-api-key": key},
        timeout=20.0,
    )
    response.raise_for_status()
    for model in response.json().get("models", []):
        methods = model.get("supportedGenerationMethods", [])
        if "generateContent" in methods:
            print(model["name"].removeprefix("models/"))


def anthropic() -> None:
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise SystemExit("ANTHROPIC_API_KEY is not set")
    response = httpx.get(
        "https://api.anthropic.com/v1/models",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        timeout=20.0,
    )
    response.raise_for_status()
    for model in response.json().get("data", []):
        print(model["id"])


def openai() -> None:
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("OPENAI_API_KEY is not set")
    response = httpx.get(
        "https://api.openai.com/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=20.0,
    )
    response.raise_for_status()
    for model in sorted(item["id"] for item in response.json().get("data", [])):
        print(model)


if __name__ == "__main__":
    provider = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("GRIDWISE_LLM_PROVIDER", "gemini")
    {"gemini": gemini, "anthropic": anthropic, "openai": openai}[provider]()
