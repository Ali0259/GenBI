"""
app.api.routes_auth
---------------------
Login endpoint for the password-protected Admin Panel. OpenUI business-user
sessions are issued by an admin provisioning flow that reuses the same
create_access_token mechanism (not shown separately -- identical pattern).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_admin_db_session
from app.models.admin_models import AdminUser
from app.schemas.api_schemas import LoginRequest, LoginResponse
from app.security import create_access_token, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
_settings = get_settings()


@router.post("/login", response_model=LoginResponse)
def login(
    login_request: LoginRequest,
    db_session: Annotated[Session, Depends(get_admin_db_session)],
) -> LoginResponse:
    admin_user = db_session.scalar(select(AdminUser).where(AdminUser.email == login_request.email))

    # Deliberately identical error for "no such user" and "wrong password" --
    # never reveal which one it was, to avoid user enumeration.
    invalid_credentials_error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password."
    )

    if admin_user is None:
        raise invalid_credentials_error

    if not verify_password(login_request.password, admin_user.hashed_password):
        raise invalid_credentials_error

    access_token = create_access_token(
        user_id=admin_user.id, tenant_id=admin_user.tenant_id, is_superadmin=admin_user.is_superadmin
    )

    return LoginResponse(
        access_token=access_token,
        expires_in_minutes=_settings.jwt_access_token_expiry_minutes,
    )
