"""
app.llm.ollama_adapter
-----------------------
Concrete BaseLLMAdapter implementation backed by a local/offline Ollama instance.

Used when the Admin Panel configures a fully offline deployment (no external
API keys, no internet egress required for inference). Talks to Ollama's
/api/generate endpoint over the internal Docker network.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.llm.base import BaseLLMAdapter, LLMAdapterError, LLMCompletionResult, LLMProvider

_SQL_PROMPT_TEMPLATE = """You are a read-only SQL generation engine for a {dialect} database.
Rules:
1. Output ONLY a single valid {dialect} SQL statement. No prose, no markdown fences.
2. The statement MUST begin with SELECT or WITH. Never generate INSERT, UPDATE, DELETE, DROP,
   ALTER, TRUNCATE, MERGE, EXEC, EXECUTE, GRANT, or REVOKE.
3. Include an explicit row limit (TOP for MSSQL, LIMIT for Postgres/MySQL/MariaDB) where sensible.
4. Only reference tables/columns present in the schema context below.

Schema context:
{schema}

Question:
{question}

SQL statement:
"""

_SQL_REPAIR_PROMPT_TEMPLATE = """You are a SQL repair engine for a {dialect} database.
The following SQL statement failed to execute.

Schema context:
{schema}

Failed SQL:
{failed_sql}

Database error:
{error}

Produce ONLY the corrected, single, read-only {dialect} SQL statement (must start with SELECT or WITH).
Corrected SQL:
"""


class OllamaAdapter(BaseLLMAdapter):
    """
    Talks to a local Ollama server's /api/generate endpoint (default
    http://ollama:11434 inside the Docker network, configurable via base_url).
    No API key is required or used.
    """

    provider = LLMProvider.OLLAMA

    async def _post_generate(self, prompt: str) -> LLMCompletionResult:
        if not self.base_url:
            raise LLMAdapterError(self.provider, "No base_url configured for Ollama adapter.")

        endpoint = f"{self.base_url.rstrip('/')}/api/generate"
        payload: dict[str, Any] = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.0},
        }

        try:
            async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
                response = await client.post(endpoint, json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMAdapterError(self.provider, "Request to local Ollama instance timed out.", exc) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMAdapterError(
                self.provider, f"HTTP {exc.response.status_code}: {exc.response.text}", exc
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMAdapterError(self.provider, "Request failed or returned invalid JSON.", exc) from exc

        try:
            content_text: str = str(data["response"]).strip()
        except (KeyError, TypeError) as exc:
            raise LLMAdapterError(self.provider, "Unexpected response shape from Ollama.", exc) from exc

        return LLMCompletionResult(
            text=content_text,
            raw_provider_payload=data,
            model_name=self.model_name,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
        )

    async def generate_sql(self, natural_language_question: str, schema_context: str,
                            sql_dialect: str) -> LLMCompletionResult:
        prompt = _SQL_PROMPT_TEMPLATE.format(
            dialect=sql_dialect, schema=schema_context, question=natural_language_question
        )
        return await self._post_generate(prompt)

    async def repair_sql(self, failed_sql: str, database_error_message: str, schema_context: str,
                          sql_dialect: str) -> LLMCompletionResult:
        prompt = _SQL_REPAIR_PROMPT_TEMPLATE.format(
            dialect=sql_dialect, schema=schema_context, failed_sql=failed_sql, error=database_error_message
        )
        return await self._post_generate(prompt)

    async def health_check(self) -> bool:
        try:
            await asyncio.wait_for(self._post_generate("Reply with the word OK."), timeout=self.request_timeout_seconds)
            return True
        except (LLMAdapterError, asyncio.TimeoutError):
            return False
