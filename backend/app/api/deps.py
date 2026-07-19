"""
app.api.deps
------------
Shared FastAPI dependencies: current-user extraction from the JWT bearer
token, the admin DB session, the credential encryptor, and per-tenant
LLMAdapterFactory lookup.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_admin_db_session
from app.llm.factory import LLMAdapterFactory
from app.models.admin_models import AdminUser, CredentialEncryptionError, CredentialEncryptor
from app.security import AuthenticationError, TokenPayload, decode_access_token

_bearer_scheme = HTTPBearer(auto_error=False)


def get_credential_encryptor() -> CredentialEncryptor:
    try:
        return CredentialEncryptor()
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server is missing a valid master encryption key configuration.",
        ) from exc


def get_current_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)]
) -> TokenPayload:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token.")
    try:
        return decode_access_token(credentials.credentials)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc


def get_current_admin_user(
    token_payload: Annotated[TokenPayload, Depends(get_current_token_payload)],
    db_session: Annotated[Session, Depends(get_admin_db_session)],
) -> AdminUser:
    admin_user = db_session.get(AdminUser, uuid.UUID(token_payload.subject_user_id))
    if admin_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User account no longer exists.")
    return admin_user


def require_superadmin(
    admin_user: Annotated[AdminUser, Depends(get_current_admin_user)]
) -> AdminUser:
    if not admin_user.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This action requires superadmin privileges.")
    return admin_user


def get_llm_adapter_factory(request: Request, token_payload: Annotated[TokenPayload, Depends(get_current_token_payload)]) -> LLMAdapterFactory:
    """
    Returns the LLMAdapterFactory for the requesting user's tenant, creating
    one lazily on app.state if this is the first request seen for that
    tenant since process start. Keyed strictly by tenant_id so hot-swapping
    a provider for one tenant never affects another.
    """
    tenant_factories: dict[str, LLMAdapterFactory] = request.app.state.llm_factories_by_tenant
    tenant_id = token_payload.tenant_id
    if tenant_id not in tenant_factories:
        tenant_factories[tenant_id] = LLMAdapterFactory()
    return tenant_factories[tenant_id]
