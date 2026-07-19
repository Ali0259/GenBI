"""
app.api.routes_query
----------------------
The single OpenUI-facing endpoint: accepts a natural language question plus
a target_connection_id, and runs the full generate -> safety-validate ->
execute -> self-correct pipeline, returning the final SQL, its results, and
a full attempt audit trail. Every call is also persisted to QueryAuditLog.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.agent.text_to_sql import TextToSqlAgent, TextToSqlExhaustedError
from app.api.deps import get_current_admin_user, get_credential_encryptor, get_llm_adapter_factory
from app.database import get_admin_db_session
from app.db.introspection import SchemaIntrospectionError, SqlDialect, fetch_schema_columns, render_schema_as_llm_context
from app.db.target_connection import TargetConnectionError, build_target_engine
from app.llm.factory import LLMAdapterFactory, LLMConfigurationError
from app.models.admin_models import AdminUser, CredentialEncryptor, QueryAuditLog, TargetDatabaseConnection
from app.schemas.api_schemas import QueryAttemptResponse, QueryRequest, QueryResponse

router = APIRouter(prefix="/api/query", tags=["query"])


@router.post("", response_model=QueryResponse)
async def run_natural_language_query(
    query_request: QueryRequest,
    db_session: Annotated[Session, Depends(get_admin_db_session)],
    admin_user: Annotated[AdminUser, Depends(get_current_admin_user)],
    encryptor: Annotated[CredentialEncryptor, Depends(get_credential_encryptor)],
    llm_factory: Annotated[LLMAdapterFactory, Depends(get_llm_adapter_factory)],
) -> QueryResponse:
    connection_row = db_session.get(TargetDatabaseConnection, query_request.target_connection_id)
    if connection_row is None or connection_row.tenant_id != admin_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target database connection not found.")
    if not connection_row.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="This connection has been deactivated.")

    try:
        active_llm_adapter = llm_factory.get_active_adapter()
    except LLMConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        target_engine = build_target_engine(connection_row, encryptor)
    except TargetConnectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    dialect = SqlDialect(connection_row.dialect)

    try:
        schema_columns = fetch_schema_columns(target_engine, dialect)
        schema_context = render_schema_as_llm_context(schema_columns)
    except SchemaIntrospectionError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    agent = TextToSqlAgent(
        llm_adapter=active_llm_adapter,
        target_engine=target_engine,
        dialect=dialect,
        schema_context=schema_context,
    )

    audit_log_entry = QueryAuditLog(
        tenant_id=admin_user.tenant_id,
        admin_user_id=admin_user.id,
        natural_language_question=query_request.natural_language_question,
        was_successful=False,
        self_correction_attempts=0,
    )

    try:
        result = await agent.run(query_request.natural_language_question)
    except TextToSqlExhaustedError as exc:
        audit_log_entry.error_message = str(exc)
        db_session.add(audit_log_entry)
        db_session.commit()
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    audit_log_entry.was_successful = True
    audit_log_entry.final_executed_sql = result.final_sql
    audit_log_entry.self_correction_attempts = len(result.attempts) - 1
    db_session.add(audit_log_entry)
    db_session.commit()

    return QueryResponse(
        final_sql=result.final_sql,
        row_results=result.row_results,
        attempts=[
            QueryAttemptResponse(
                attempt_number=a.attempt_number,
                sql_text=a.sql_text,
                succeeded=a.succeeded,
                error_message=a.error_message,
            )
            for a in result.attempts
        ],
    )
