"""
app.llm.factory
----------------
Runtime hot-swap manager for LLM adapters. Supports all four providers named
in the platform spec: OpenAI, Anthropic, Gemini, and local/offline Ollama.

The Admin Panel calls `LLMAdapterFactory.set_active_configuration(...)` when
an operator switches providers or models. Every in-flight and future request
that asks the factory for `get_active_adapter()` immediately receives the
new adapter instance -- no FastAPI process restart, no dropped connections.

Thread/async-safety: an `asyncio.Lock` guards the swap operation so a
request cannot read a half-updated state while a swap is in progress.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from app.llm.anthropic_adapter import AnthropicAdapter
from app.llm.base import BaseLLMAdapter, LLMProvider
from app.llm.gemini_adapter import GeminiAdapter
from app.llm.ollama_adapter import OllamaAdapter
from app.llm.openai_adapter import OpenAIAdapter


class LLMConfigurationError(Exception):
    """Raised when an operator attempts to activate an invalid or unreachable LLM configuration."""


class LLMAdapterFactory:
    """
    Holds exactly one "active" adapter at a time for a given tenant context.
    In a multi-tenant deployment, instantiate one factory per tenant (keyed
    in an in-memory registry on app.state, keyed by tenant_id) rather than
    sharing a single global instance across tenants.
    """

    def __init__(self) -> None:
        self._active_adapter: Optional[BaseLLMAdapter] = None
        self._swap_lock = asyncio.Lock()

    @staticmethod
    def _build_adapter(provider: LLMProvider, model_name: str, api_key: Optional[str],
                        base_url: Optional[str], request_timeout_seconds: float) -> BaseLLMAdapter:
        """Pure factory method: given validated configuration, construct the correct concrete adapter."""
        if provider == LLMProvider.OPENAI:
            return OpenAIAdapter(model_name=model_name, api_key=api_key, base_url=base_url,
                                  request_timeout_seconds=request_timeout_seconds)
        if provider == LLMProvider.ANTHROPIC:
            return AnthropicAdapter(model_name=model_name, api_key=api_key, base_url=base_url,
                                     request_timeout_seconds=request_timeout_seconds)
        if provider == LLMProvider.GEMINI:
            return GeminiAdapter(model_name=model_name, api_key=api_key, base_url=base_url,
                                  request_timeout_seconds=request_timeout_seconds)
        if provider == LLMProvider.OLLAMA:
            return OllamaAdapter(model_name=model_name, api_key=None, base_url=base_url,
                                  request_timeout_seconds=request_timeout_seconds)

        raise LLMConfigurationError(
            f"Provider '{provider.value}' has no registered adapter implementation."
        )

    async def set_active_configuration(self, provider: LLMProvider, model_name: str,
                                        api_key: Optional[str] = None, base_url: Optional[str] = None,
                                        request_timeout_seconds: float = 30.0,
                                        verify_before_swap: bool = True) -> None:
        """
        Hot-swap the active adapter. If `verify_before_swap` is True (default,
        strongly recommended for production), the new adapter's
        `health_check()` must pass before the swap is committed -- this
        prevents an operator typo (bad API key, unreachable Ollama host) from
        silently taking the whole GenBI feature offline for every user.
        """
        candidate_adapter = self._build_adapter(provider, model_name, api_key, base_url, request_timeout_seconds)

        if verify_before_swap:
            is_healthy = await candidate_adapter.health_check()
            if not is_healthy:
                raise LLMConfigurationError(
                    f"Health check failed for provider='{provider.value}' model='{model_name}'. "
                    f"Refusing to activate this configuration; the previously active adapter remains in place."
                )

        async with self._swap_lock:
            self._active_adapter = candidate_adapter

    def get_active_adapter(self) -> BaseLLMAdapter:
        if self._active_adapter is None:
            raise LLMConfigurationError(
                "No LLM adapter has been configured yet. An administrator must configure "
                "a provider in the Admin Panel before GenBI queries can be served."
            )
        return self._active_adapter
