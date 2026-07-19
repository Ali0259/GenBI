"""
app.llm.gemini_adapter
-----------------------
Concrete BaseLLMAdapter implementation backed by the Google Gemini
generateContent REST API.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.llm.base import BaseLLMAdapter, LLMAdapterError, LLMCompletionResult, LLMProvider

_SQL_PROMPT_TEMPLATE = """You are a read-only SQL generation engine for a {dialect} database.
Rules you must follow without exception:
1. Output ONLY a single valid {dialect} SQL statement. No prose, no markdown fences, no explanation.
2. The statement MUST begin with SELECT or WITH. Never generate INSERT, UPDATE, DELETE, DROP,
   ALTER, TRUNCATE, MERGE, EXEC, EXECUTE, GRANT, REVOKE, or any DDL/DML statement.
3. Always include an explicit row limit appropriate to the dialect (TOP for MSSQL, LIMIT for
   Postgres/MySQL/MariaDB) unless the question clearly requires an aggregate with no row list.
4. Only reference tables and columns that appear in the provided schema context.

Schema context:
{schema}

Question:
{question}
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
No prose, no markdown fences.
"""


class GeminiAdapter(BaseLLMAdapter):
    """
    Talks to the Google Generative Language API
    (generativelanguage.googleapis.com). Requires an API key, passed as a
    query parameter per Google's REST convention.
    """

    provider = LLMProvider.GEMINI

    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    async def _post_generate_content(self, prompt_text: str) -> LLMCompletionResult:
        if not self.api_key:
            raise LLMAdapterError(self.provider, "No API key configured for Gemini adapter.")

        endpoint = f"{self._BASE_URL}/{self.model_name}:generateContent"
        payload: dict[str, Any] = {
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {"temperature": 0.0},
        }
        params = {"key": self.api_key}

        try:
            async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
                response = await client.post(endpoint, json=payload, params=params)
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
            candidate = data["candidates"][0]
            content_text = "".join(
                part.get("text", "") for part in candidate["content"]["parts"]
            ).strip()
            usage = data.get("usageMetadata", {})
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMAdapterError(self.provider, "Unexpected response shape from Gemini.", exc) from exc

        return LLMCompletionResult(
            text=content_text,
            raw_provider_payload=data,
            model_name=self.model_name,
            input_tokens=usage.get("promptTokenCount"),
            output_tokens=usage.get("candidatesTokenCount"),
        )

    async def generate_sql(self, natural_language_question: str, schema_context: str,
                            sql_dialect: str) -> LLMCompletionResult:
        prompt = _SQL_PROMPT_TEMPLATE.format(
            dialect=sql_dialect, schema=schema_context, question=natural_language_question
        )
        return await self._post_generate_content(prompt)

    async def repair_sql(self, failed_sql: str, database_error_message: str, schema_context: str,
                          sql_dialect: str) -> LLMCompletionResult:
        prompt = _SQL_REPAIR_PROMPT_TEMPLATE.format(
            dialect=sql_dialect, schema=schema_context, failed_sql=failed_sql, error=database_error_message
        )
        return await self._post_generate_content(prompt)

    async def health_check(self) -> bool:
        try:
            await asyncio.wait_for(
                self._post_generate_content("Reply with the word OK."), timeout=self.request_timeout_seconds
            )
            return True
        except (LLMAdapterError, asyncio.TimeoutError):
            return False
