"""
app.llm.base
------------
Defines the provider-agnostic contract that every LLM adapter must satisfy.

The GenBI engine never talks to OpenAI, Anthropic, Gemini, or Ollama directly.
It only ever talks to a `BaseLLMAdapter`. This is what makes runtime hot-swapping
possible: the active adapter instance can be replaced by the LLMAdapterFactory
without restarting the FastAPI process or breaking any in-flight abstractions.

Design notes:
- All adapters are async, since FastAPI request handlers should never block
  the event loop on network I/O.
- `generate_sql` and `repair_sql` are separated deliberately. Repair prompts
  are structurally different (they include the failed SQL + the DB error),
  and keeping them as distinct methods lets each adapter tune its own
  system prompt / temperature per use case.
- Adapters MUST raise `LLMAdapterError` (never a raw provider exception) so
  the rest of the system can handle failures uniformly regardless of which
  provider is currently active.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


class LLMProvider(str, Enum):
    """Enumerates every LLM backend the platform knows how to speak to."""

    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    OLLAMA = "ollama"


class LLMAdapterError(Exception):
    """
    Raised by any adapter when the underlying provider call fails.

    Wrapping every provider-specific exception (httpx errors, SDK-specific
    exceptions, timeouts) in this single type means the calling code in
    app.agent.text_to_sql never needs to know which provider is active.
    """

    def __init__(self, provider: LLMProvider, message: str, original_exception: Optional[Exception] = None) -> None:
        self.provider = provider
        self.original_exception = original_exception
        super().__init__(f"[{provider.value}] {message}")


@dataclass(frozen=True)
class LLMCompletionResult:
    """Normalized response shape returned by every adapter, regardless of provider."""

    text: str
    raw_provider_payload: dict[str, Any]
    model_name: str
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None


class BaseLLMAdapter(abc.ABC):
    """
    Abstract base class that every concrete LLM provider adapter must implement.

    Concrete implementations: OpenAIAdapter, OllamaAdapter (see
    app.llm.openai_adapter and app.llm.ollama_adapter). Anthropic and Gemini
    adapters follow the identical pattern and are intentionally omitted here
    to keep the skeleton focused, but they plug into the same factory.
    """

    provider: LLMProvider

    def __init__(self, model_name: str, api_key: Optional[str] = None, base_url: Optional[str] = None,
                 request_timeout_seconds: float = 30.0) -> None:
        self.model_name = model_name
        self.api_key = api_key
        self.base_url = base_url
        self.request_timeout_seconds = request_timeout_seconds

    @abc.abstractmethod
    async def generate_sql(self, natural_language_question: str, schema_context: str,
                            sql_dialect: str) -> LLMCompletionResult:
        """
        Generate a single SQL SELECT statement for the given dialect from a
        natural language question, grounded in the supplied schema context.

        Implementations MUST instruct the underlying model, via system prompt,
        to return read-only SQL only. This is a defense-in-depth measure —
        the authoritative safety gate is the AST parser in
        app.agent.text_to_sql.SafetySandbox, not this instruction alone.
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def repair_sql(self, failed_sql: str, database_error_message: str, schema_context: str,
                          sql_dialect: str) -> LLMCompletionResult:
        """
        Given a SQL statement that failed to execute and the raw database
        error, ask the model to produce a corrected statement. Used by the
        self-correction loop in TextToSqlAgent (max 3 attempts).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def health_check(self) -> bool:
        """
        Lightweight liveness probe used by the Admin Panel to verify a
        configured provider is reachable before it is hot-swapped in as the
        active adapter. Must not raise; returns False on any failure.
        """
        raise NotImplementedError
