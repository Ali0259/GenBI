"""
app.schemas.api_schemas
------------------------
Pydantic models defining every request and response shape exposed by the
FastAPI routes. Kept separate from the SQLAlchemy ORM models in
app.models.admin_models so that internal-only fields (ciphertext columns,
hashed passwords) can never accidentally leak into an API response --
these schemas only ever expose what is explicitly declared here.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_minutes: int


class TenantCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    is_active: bool
    created_at: datetime.datetime


class TargetDatabaseConnectionCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=255)
    dialect: str = Field(description="One of: mssql, postgresql, mysql, mariadb")
    host: str
    port: int = Field(gt=0, lt=65536)
    database_name: str
    read_only_username: str
    plaintext_password: str = Field(
        description="Never stored as-is. Encrypted with the master key immediately upon receipt."
    )
    connect_timeout_seconds: int = Field(default=10, gt=0)
    statement_timeout_seconds: int = Field(default=30, gt=0)


class TargetDatabaseConnectionResponse(BaseModel):
    """
    Deliberately excludes encrypted_connection_secret entirely -- this
    schema defines the full set of fields ever returned to a client, and
    that field is not one of them.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    display_name: str
    dialect: str
    host: str
    port: int
    database_name: str
    read_only_username: str
    is_active: bool
    created_at: datetime.datetime
    updated_at: datetime.datetime


class LlmConfigurationCreateRequest(BaseModel):
    provider: str = Field(description="One of: openai, anthropic, gemini, ollama")
    model_name: str
    base_url: Optional[str] = Field(default=None, description="Required for Ollama, e.g. http://ollama:11434")
    plaintext_api_key: Optional[str] = Field(
        default=None, description="Not required for Ollama. Encrypted with the master key immediately upon receipt."
    )
    activate_immediately: bool = Field(default=True)


class LlmConfigurationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    model_name: str
    base_url: Optional[str]
    is_currently_active: bool
    created_at: datetime.datetime


class QueryRequest(BaseModel):
    target_connection_id: uuid.UUID
    natural_language_question: str = Field(min_length=1, max_length=2000)


class QueryAttemptResponse(BaseModel):
    attempt_number: int
    sql_text: str
    succeeded: bool
    error_message: Optional[str] = None


class QueryResponse(BaseModel):
    final_sql: str
    row_results: list[dict[str, Any]]
    attempts: list[QueryAttemptResponse]
