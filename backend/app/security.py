"""
app.security
------------
Password hashing (argon2id, via passlib) and JWT issuance/verification for
the password-protected Admin Panel. OpenUI business-user sessions use the
same JWT mechanism, issued after an admin provisions their account.

This module never handles target-database or LLM-provider credentials --
those are handled exclusively by CredentialEncryptor in app.models.admin_models,
which uses a completely different mechanism (Fernet symmetric encryption,
not hashing) because those secrets must be recoverable in plaintext at
connection time, whereas passwords must never be recoverable at all.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Any, Optional

import jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from app.config import get_settings

_settings = get_settings()

_password_hashing_context = CryptContext(schemes=["argon2"], deprecated="auto")


class TokenPayload(BaseModel):
    """Decoded, validated shape of a GenBI session JWT."""

    subject_user_id: str
    tenant_id: str
    is_superadmin: bool
    expires_at: datetime.datetime


class AuthenticationError(Exception):
    """Raised for any password verification or token validation failure."""


def hash_password(plaintext_password: str) -> str:
    return _password_hashing_context.hash(plaintext_password)


def verify_password(plaintext_password: str, hashed_password: str) -> bool:
    try:
        return _password_hashing_context.verify(plaintext_password, hashed_password)
    except ValueError:
        # Raised by passlib on a malformed hash -- treat as a failed verification,
        # not a server error, so we don't leak information about why it failed.
        return False


def create_access_token(user_id: uuid.UUID, tenant_id: uuid.UUID, is_superadmin: bool) -> str:
    now = datetime.datetime.now(datetime.timezone.utc)
    expires_at = now + datetime.timedelta(minutes=_settings.jwt_access_token_expiry_minutes)

    claims: dict[str, Any] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "is_superadmin": is_superadmin,
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(claims, _settings.genbi_jwt_signing_key, algorithm=_settings.jwt_algorithm)


def decode_access_token(token: str) -> TokenPayload:
    try:
        decoded_claims = jwt.decode(token, _settings.genbi_jwt_signing_key, algorithms=[_settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Session token has expired. Please log in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Session token is invalid or has been tampered with.") from exc

    try:
        return TokenPayload(
            subject_user_id=decoded_claims["sub"],
            tenant_id=decoded_claims["tenant_id"],
            is_superadmin=bool(decoded_claims["is_superadmin"]),
            expires_at=datetime.datetime.fromtimestamp(decoded_claims["exp"], tz=datetime.timezone.utc),
        )
    except KeyError as exc:
        raise AuthenticationError("Session token is missing required claims.") from exc
