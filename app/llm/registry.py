"""Provider chain assembly.

The chain is always primary language model first, optional second language model second. There is
no third stage: when every language-model provider fails the request ends in a controlled error,
because a non-model interpreter would breach the mandatory-LLM requirement.
"""

from __future__ import annotations

import httpx

from app.config import Settings
from app.llm.base import LLMProvider, ProviderNotConfigured
from app.llm.providers import build_provider


def build_chain(settings: Settings, client: httpx.AsyncClient) -> list[LLMProvider]:
    """Build the ordered provider chain from configuration, skipping unconfigured entries."""
    chain: list[LLMProvider] = []
    for provider_name, model in settings.configured_providers():
        try:
            chain.append(
                build_provider(provider_name, model, settings.api_key_for(provider_name), client)
            )
        except ProviderNotConfigured:
            continue
    return chain
