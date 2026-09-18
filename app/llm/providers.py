"""Concrete language-model providers.

Each provider sends one request containing every operator note and asks for provider-enforced
structured output. Credentials arrive from the environment through `Settings` and are never logged
or echoed.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.llm.base import (
    LLMProvider,
    ProviderError,
    ProviderNotConfigured,
    RateLimited,
    RawInterpretation,
    flat_entries_to_interpretation,
)
from app.llm.prompt import (
    DIRECTIVE_TYPE_VALUES,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.schemas import Battery

_NULLABLE_NUMBERS = ("factor", "minimum_energy_kwh", "max_grid_kwh")


def _gemini_schema() -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "OBJECT",
        "properties": {
            "note_index": {"type": "INTEGER"},
            "applies": {"type": "BOOLEAN"},
            "directive_type": {"type": "STRING", "enum": DIRECTIVE_TYPE_VALUES},
            "hours": {"type": "ARRAY", "items": {"type": "INTEGER"}},
            "explanation": {"type": "STRING"},
        },
        "required": ["note_index", "applies", "directive_type", "hours", "explanation"],
        "propertyOrdering": [
            "note_index",
            "applies",
            "directive_type",
            "hours",
            "factor",
            "minimum_energy_kwh",
            "max_grid_kwh",
            "explanation",
        ],
    }
    for field in _NULLABLE_NUMBERS:
        entry["properties"][field] = {"type": "NUMBER", "nullable": True}
    return {"type": "ARRAY", "items": entry}


def _json_schema() -> dict[str, Any]:
    """JSON Schema form used by the Anthropic and OpenAI providers."""
    properties: dict[str, Any] = {
        "note_index": {"type": "integer"},
        "applies": {"type": "boolean"},
        "directive_type": {"type": "string", "enum": DIRECTIVE_TYPE_VALUES},
        "hours": {"type": "array", "items": {"type": "integer"}},
        "explanation": {"type": "string"},
    }
    for field in _NULLABLE_NUMBERS:
        properties[field] = {"type": ["number", "null"]}
    return {
        "type": "object",
        "properties": {
            "interpretation": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
        },
        "required": ["interpretation"],
        "additionalProperties": False,
    }


def _raise_for_status(provider: str, response: httpx.Response) -> None:
    """Turn a non-200 into the right error type, without leaking the provider payload."""
    if response.status_code == 200:
        return
    if response.status_code == 429:
        header = response.headers.get("retry-after")
        try:
            retry_after = float(header) if header is not None else None
        except ValueError:
            retry_after = None
        raise RateLimited(f"{provider} rate limited (HTTP 429)", retry_after)
    raise ProviderError(f"{provider} returned HTTP {response.status_code}")


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - provider-side anomaly
        raise ProviderError(f"model returned text that is not valid JSON: {exc.msg}") from exc


class GeminiProvider:
    name = "gemini"

    def __init__(self, model: str, api_key: str | None, client: httpx.AsyncClient) -> None:
        if not api_key:
            raise ProviderNotConfigured("GEMINI_API_KEY is not set")
        self._model = model
        self._api_key = api_key
        self._client = client

    async def interpret(
        self, notes: list[str], battery: Battery, repair_feedback: str | None = None
    ) -> RawInterpretation:
        payload = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": build_user_prompt(notes, battery, repair_feedback)}],
                }
            ],
            "generationConfig": {
                "temperature": 0,
                "responseMimeType": "application/json",
                "responseSchema": _gemini_schema(),
            },
        }
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self._model}:generateContent"
        try:
            response = await self._client.post(
                url, json=payload, headers={"x-goog-api-key": self._api_key}
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"gemini transport error: {type(exc).__name__}") from exc

        _raise_for_status("gemini", response)

        body = response.json()
        try:
            text = body["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("gemini response envelope was not in the expected shape") from exc

        flat = _parse_json(text)
        if not isinstance(flat, list):
            raise ProviderError("gemini did not return a JSON array of interpretation entries")
        return RawInterpretation(
            entries=flat_entries_to_interpretation(flat), provider=self.name, model=self._model
        )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, api_key: str | None, client: httpx.AsyncClient) -> None:
        if not api_key:
            raise ProviderNotConfigured("ANTHROPIC_API_KEY is not set")
        self._model = model
        self._api_key = api_key
        self._client = client

    async def interpret(
        self, notes: list[str], battery: Battery, repair_feedback: str | None = None
    ) -> RawInterpretation:
        tool = {
            "name": "submit_interpretation",
            "description": "Return one interpretation entry per operator note.",
            "input_schema": _json_schema(),
        }
        payload = {
            "model": self._model,
            "max_tokens": 1500,
            "temperature": 0,
            "system": SYSTEM_PROMPT,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": "submit_interpretation"},
            "messages": [
                {"role": "user", "content": build_user_prompt(notes, battery, repair_feedback)}
            ],
        }
        try:
            response = await self._client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"anthropic transport error: {type(exc).__name__}") from exc

        _raise_for_status("anthropic", response)

        body = response.json()
        for block in body.get("content", []):
            if block.get("type") == "tool_use":
                flat = block.get("input", {}).get("interpretation")
                if not isinstance(flat, list):
                    raise ProviderError("anthropic tool input did not contain an array")
                return RawInterpretation(
                    entries=flat_entries_to_interpretation(flat),
                    provider=self.name,
                    model=self._model,
                )
        raise ProviderError("anthropic response contained no tool_use block")


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str, api_key: str | None, client: httpx.AsyncClient) -> None:
        if not api_key:
            raise ProviderNotConfigured("OPENAI_API_KEY is not set")
        self._model = model
        self._api_key = api_key
        self._client = client

    async def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
            )
        except httpx.HTTPError as exc:
            raise ProviderError(f"openai transport error: {type(exc).__name__}") from exc

    async def interpret(
        self, notes: list[str], battery: Battery, repair_feedback: str | None = None
    ) -> RawInterpretation:
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(notes, battery, repair_feedback)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "interpretation",
                    "strict": True,
                    "schema": _json_schema(),
                },
            },
        }
        response = await self._post(payload)

        if response.status_code == 400 and _rejects_temperature(response):
            # Newer models accept only the default temperature. Drop the field and retry once:
            # determinism still comes from the strict schema and the fixed prompt.
            payload.pop("temperature", None)
            response = await self._post(payload)

        _raise_for_status("openai", response)

        body = response.json()
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("openai response envelope was not in the expected shape") from exc

        parsed = _parse_json(text)
        flat = parsed.get("interpretation") if isinstance(parsed, dict) else None
        if not isinstance(flat, list):
            raise ProviderError("openai did not return an interpretation array")
        return RawInterpretation(
            entries=flat_entries_to_interpretation(flat), provider=self.name, model=self._model
        )


def _rejects_temperature(response: httpx.Response) -> bool:
    """True when the model refused the request only because `temperature` was supplied."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return False
    return error.get("param") == "temperature"


PROVIDERS: dict[str, type] = {
    "gemini": GeminiProvider,
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}


def build_provider(
    provider_name: str, model: str, api_key: str | None, client: httpx.AsyncClient
) -> LLMProvider:
    factory = PROVIDERS.get(provider_name)
    if factory is None:
        raise ProviderNotConfigured(f"unknown provider '{provider_name}'")
    return factory(model, api_key, client)
