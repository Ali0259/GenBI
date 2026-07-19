"""
app.api.routes_admin
----------------------
Admin Panel-facing routes: tenant management, target database connection
CRUD (with encryption on write, never plaintext on read), and LLM provider
configuration including the hot-swap activation flow.

All routes here require a valid Admin Panel session (see app.api.deps).
Tenant creation is restricted to superadmins; connection and LLM config
management is scoped to the requesting admin's own tenant.
"""

from __future__ import annotations

import json
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_admin_user, get_credential_encryptor, get_llm_adapter_factory, require_superadmin
from app.database import get_admin_db_session
from app.db.introspection import SqlDialect
from app.llm.base import LLMProvider
from app.llm.factory import LLMAdapterFactory, LLMConfigurationError
from app.models.admin_models import AdminUser, CredentialEncryptor, LlmConfiguration, Tenant, TargetDatabaseConnection
from app.schemas.api_schemas import (
    LlmConfigurationCreateRequest,
    LlmConfigurationResponse,
    TargetDatabaseConnectionCreateRequest,
    TargetDatabaseConnectionResponse,
    TenantCreateRequest,
    TenantResponse,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# Tenants
# ---------------------------------------------------------------------------

@router.post("/tenants", response_model=TenantResponse, status_code=status.HTTP_201_CREATED)
def create_tenant(
    tenant_request: TenantCreateRequest,
    db_session: Annotated[Session, Depends(get_admin_db_session)],
    _superadmin: Annotated[AdminUser, Depends(require_superadmin)],
) -> Tenant:
    new_tenant = Tenant(display_name=tenant_request.display_name)
    db_session.add(new_tenant)
    db_session.commit()
    db_session.refresh(new_tenant)
    return new_tenant


# ---------------------------------------------------------------------------
# Target database connections
# ---------------------------------------------------------------------------

@router.post(
    "/connections",
    response_model=TargetDatabaseConnectionResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_target_database_connection(
    connection_request: TargetDatabaseConnectionCreateRequest,
    db_session: Annotated[Session, Depends(get_admin_db_session)],
    admin_user: Annotated[AdminUser, Depends(get_current_admin_user)],
    encryptor: Annotated[CredentialEncryptor, Depends(get_credential_encryptor)],
) -> TargetDatabaseConnection:
    try:
        SqlDialect(connection_request.dialect)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported dialect '{connection_request.dialect}'. "
                   f"Must be one of: {', '.join(d.value for d in SqlDialect)}.",
        ) from exc

    encrypted_secret = encryptor.encrypt(json.dumps({"password": connection_request.plaintext_password}))

    new_connection = TargetDatabaseConnection(
        tenant_id=admin_user.tenant_id,
        display_name=connection_request.display_name,
        dialect=connection_request.dialect,
        host=connection_request.host,
        port=connection_request.port,
        database_name=connection_request.database_name,
        read_only_username=connection_request.read_only_username,
        encrypted_connection_secret=encrypted_secret,
        connect_timeout_seconds=connection_request.connect_timeout_seconds,
        statement_timeout_seconds=connection_request.statement_timeout_seconds,
    )
    db_session.add(new_connection)
    db_session.commit()
    db_session.refresh(new_connection)
    return new_connection


@router.get("/connections", response_model=list[TargetDatabaseConnectionResponse])
def list_target_database_connections(
    db_session: Annotated[Session, Depends(get_admin_db_session)],
    admin_user: Annotated[AdminUser, Depends(get_current_admin_user)],
) -> list[TargetDatabaseConnection]:
    statement = select(TargetDatabaseConnection).where(TargetDatabaseConnection.tenant_id == admin_user.tenant_id)
    return list(db_session.scalars(statement).all())


# ---------------------------------------------------------------------------
# LLM configuration (hot-swap)
# ---------------------------------------------------------------------------

@router.post(
    "/llm-configurations",
    response_model=LlmConfigurationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_and_optionally_activate_llm_configuration(
    llm_request: LlmConfigurationCreateRequest,
    db_session: Annotated[Session, Depends(get_admin_db_session)],
    admin_user: Annotated[AdminUser, Depends(get_current_admin_user)],
    encryptor: Annotated[CredentialEncryptor, Depends(get_credential_encryptor)],
    llm_factory: Annotated[LLMAdapterFactory, Depends(get_llm_adapter_factory)],
) -> LlmConfiguration:
    try:
        provider_enum = LLMProvider(llm_request.provider)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported provider '{llm_request.provider}'. "
                   f"Must be one of: {', '.join(p.value for p in LLMProvider)}.",
        ) from exc

    if provider_enum != LLMProvider.OLLAMA and not llm_request.plaintext_api_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Provider '{provider_enum.value}' requires an API key.",
        )

    encrypted_api_key = (
        encryptor.encrypt(llm_request.plaintext_api_key) if llm_request.plaintext_api_key else None
    )

    new_configuration = LlmConfiguration(
        tenant_id=admin_user.tenant_id,
        provider=provider_enum.value,
        model_name=llm_request.model_name,
        base_url=llm_request.base_url,
        encrypted_api_key=encrypted_api_key,
        is_currently_active=False,
    )
    db_session.add(new_configuration)
    db_session.commit()
    db_session.refresh(new_configuration)

    if llm_request.activate_immediately:
        try:
            await llm_factory.set_active_configuration(
                provider=provider_enum,
                model_name=llm_request.model_name,
                api_key=llm_request.plaintext_api_key,
                base_url=llm_request.base_url,
                verify_before_swap=True,
            )
        except LLMConfigurationError as exc:
            # The row is still saved for later retry, but we surface the
            # health-check failure clearly rather than silently succeeding.
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

        db_session.query(LlmConfiguration).filter(
            LlmConfiguration.tenant_id == admin_user.tenant_id,
            LlmConfiguration.id != new_configuration.id,
        ).update({"is_currently_active": False})
        new_configuration.is_currently_active = True
        db_session.commit()
        db_session.refresh(new_configuration)

    return new_configuration


@router.get("/llm-configurations", response_model=list[LlmConfigurationResponse])
def list_llm_configurations(
    db_session: Annotated[Session, Depends(get_admin_db_session)],
    admin_user: Annotated[AdminUser, Depends(get_current_admin_user)],
) -> list[LlmConfiguration]:
    statement = select(LlmConfiguration).where(LlmConfiguration.tenant_id == admin_user.tenant_id)
    return list(db_session.scalars(statement).all())
