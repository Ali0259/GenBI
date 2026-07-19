"""
app.llm.openai_adapter
-----------------------
Concrete BaseLLMAdapter implementation backed by the OpenAI Chat Completions API.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.llm.base import BaseLLMAdapter, LLMAdapterError, LLMCompletionResult, LLMProvider

_SQL_SYSTEM_PROMPT_TEMPLATE = """You are a read-only SQL generation engine for a {dialect} database.
Rules you must follow without exception:
1. Output ONLY a single valid {dialect} SQL statement. No prose, no markdown fences, no explanation.
2. The statement MUST begin with SELECT or WITH. Never generate INSERT, UPDATE, DELETE, DROP,
   ALTER, TRUNCATE, MERGE, EXEC, EXECUTE, GRANT, REVOKE, or any DDL/DML statement.
3. Always include an explicit row limit appropriate to the dialect (TOP for MSSQL, LIMIT for
   Postgres/MySQL/MariaDB) unless the question clearly requires an aggregate with no row list.
4. Only reference tables and columns that appear in the provided schema context. Never invent
   identifiers.

Schema context:
{schema}
"""

_SQL_REPAIR_SYSTEM_PROMPT_TEMPLATE = """You are a SQL repair engine for a {dialect} database.
You will be given a SQL statement that failed to execute and the exact database error message.
Produce a corrected, single, read-only {dialect} SQL statement that fixes the error.
Output ONLY the corrected SQL statement. No prose, no markdown fences, no explanation.
The statement MUST begin with SELECT or WITH and must never contain DML or DDL.

Schema context:
{schema}

Failed SQL:
{failed_sql}

Database error:
{error}
"""


class OpenAIAdapter(BaseLLMAdapter):
    """
    Talks to the OpenAI /v1/chat/completions endpoint. Requires an API key.

    This adapter deliberately uses raw httpx rather than the openai-python
    SDK so the dependency footprint stays small and predictable across the
    Docker image, and so timeout/retry behavior is fully under our control.
    """

    provider = LLMProvider.OPENAI

    _CHAT_COMPLETIONS_URL = "https://api.openai.com/v1/chat/completions"

    async def _post_chat_completion(self, system_prompt: str, user_prompt: str) -> LLMCompletionResult:
        if not self.api_key:
            raise LLMAdapterError(self.provider, "No API key configured for OpenAI adapter.")

        payload: dict[str, Any] = {
            "model": self.model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
                response = await client.post(self._CHAT_COMPLETIONS_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.TimeoutException as exc:
            raise LLMAdapterError(self.provider, "Request timed out.", exc) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMAdapterError(
                self.provider, f"HTTP {exc.response.status_code}: {exc.response.text}", exc
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMAdapterError(self.provider, "Request failed or returned invalid JSON.", exc) from exc

        try:
            content_text: str = data["choices"][0]["message"]["content"].strip()
            usage = data.get("usage", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMAdapterError(self.provider, "Unexpected response shape from OpenAI.", exc) from exc

        return LLMCompletionResult(
            text=content_text,
            raw_provider_payload=data,
            model_name=self.model_name,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )

    async def generate_sql(self, natural_language_question: str, schema_context: str,
                            sql_dialect: str) -> LLMCompletionResult:
        system_prompt = _SQL_SYSTEM_PROMPT_TEMPLATE.format(dialect=sql_dialect, schema=schema_context)
        return await self._post_chat_completion(system_prompt, natural_language_question)

    async def repair_sql(self, failed_sql: str, database_error_message: str, schema_context: str,
                          sql_dialect: str) -> LLMCompletionResult:
        system_prompt = _SQL_REPAIR_SYSTEM_PROMPT_TEMPLATE.format(
            dialect=sql_dialect, schema=schema_context, failed_sql=failed_sql, error=database_error_message
        )
        return await self._post_chat_completion(system_prompt, "Repair the SQL statement above.")

    async def health_check(self) -> bool:
        try:
            await asyncio.wait_for(
                self._post_chat_completion("You are a health check probe.", "Reply with the word OK."),
                timeout=self.request_timeout_seconds,
            )
            return True
        except (LLMAdapterError, asyncio.TimeoutError):
            return False
