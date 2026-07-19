"""
app.models.admin_models
-------------------------
SQLAlchemy ORM models for the platform's own internal Admin Database
(Postgres, managed by Alembic -- see deploy/alembic for migration files,
not included in this skeleton).

Critical design decision: target-database credentials and LLM API keys are
NEVER stored as plaintext columns. They are stored as ciphertext produced by
a Fernet symmetric encryption key that is injected at deploy time as an
environment variable / Docker secret (GENBI_MASTER_ENCRYPTION_KEY) and is
NEVER itself persisted in this database. This means:
  - A stolen database backup is useless without the separately-managed key.
  - Rotating the key is a key-rewrap operation (decrypt with old key, encrypt
    with new key), not a schema migration -- it never touches Alembic.

All models inherit from a common Base and use `mapped_column` / `Mapped`
typing (SQLAlchemy 2.0 style) for full static type-checking support.
"""

from __future__ import annotations

import datetime
import os
import uuid
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class CredentialEncryptionError(Exception):
    """Raised when encryption or decryption of a stored credential fails."""


class CredentialEncryptor:
    """
    Thin wrapper around Fernet symmetric encryption for credential fields.

    The master key MUST be supplied via the GENBI_MASTER_ENCRYPTION_KEY
    environment variable at process startup (injected as a Docker secret in
    production -- see install.sh). This class deliberately does not read
    from any file location that would be included in a database backup.
    """

    def __init__(self, master_key_env_var: str = "GENBI_MASTER_ENCRYPTION_KEY") -> None:
        raw_key = os.environ.get(master_key_env_var)
        if not raw_key:
            raise CredentialEncryptionError(
                f"Environment variable '{master_key_env_var}' is not set. The application "
                f"cannot start without a master encryption key for stored credentials."
            )
        try:
            self._fernet = Fernet(raw_key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise CredentialEncryptionError(
                f"Environment variable '{master_key_env_var}' does not contain a valid Fernet key."
            ) from exc

    def encrypt(self, plaintext_value: str) -> str:
        try:
            return self._fernet.encrypt(plaintext_value.encode("utf-8")).decode("utf-8")
        except Exception as exc:  # noqa: BLE001 - encryption library exceptions are opaque; wrap uniformly
            raise CredentialEncryptionError(f"Failed to encrypt value: {exc}") from exc

    def decrypt(self, ciphertext_value: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext_value.encode("utf-8")).decode("utf-8")
        except InvalidToken as exc:
            raise CredentialEncryptionError(
                "Failed to decrypt stored credential -- the ciphertext is invalid or was "
                "encrypted with a different master key. Check for key rotation mismatches."
            ) from exc


class Base(DeclarativeBase):
    """Shared declarative base for every Admin Database ORM model."""


def _new_uuid() -> uuid.UUID:
    return uuid.uuid4()


class Tenant(Base):
    """A single customer organization using the platform."""

    __tablename__ = "tenants"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    database_connections: Mapped[list["TargetDatabaseConnection"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )
    llm_configurations: Mapped[list["LlmConfiguration"]] = relationship(
        back_populates="tenant", cascade="all, delete-orphan"
    )


class AdminUser(Base):
    """A human operator with access to the password-protected Admin Panel."""

    __tablename__ = "admin_users"
    __table_args__ = (UniqueConstraint("email", name="uq_admin_users_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)

    # Password hash only -- produced with a strong adaptive hashing algorithm
    # (e.g. argon2id via the `argon2-cffi` package) at the auth-service layer.
    # This model never receives or stores a plaintext password.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    is_superadmin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class TargetDatabaseConnection(Base):
    """
    A single configured connection to an external target database
    (MSSQL / PostgreSQL / MySQL / MariaDB) that GenBI will query on behalf
    of a tenant's business users.

    `encrypted_connection_secret` holds ciphertext produced by
    CredentialEncryptor -- it stores a JSON-encoded payload of whatever
    fields the specific driver needs (password, and optionally a full DSN),
    never a plaintext value.
    """

    __tablename__ = "target_database_connections"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dialect: Mapped[str] = mapped_column(String(32), nullable=False)  # 'mssql' | 'postgresql' | 'mysql' | 'mariadb'
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(nullable=False)
    database_name: Mapped[str] = mapped_column(String(255), nullable=False)
    read_only_username: Mapped[str] = mapped_column(String(255), nullable=False)

    # Ciphertext only. See CredentialEncryptor. Never query, log, or serialize
    # this column in any API response -- it should be write-only from the
    # application's perspective except at the moment a connection is opened.
    encrypted_connection_secret: Mapped[str] = mapped_column(Text, nullable=False)

    connect_timeout_seconds: Mapped[int] = mapped_column(nullable=False, default=10)
    statement_timeout_seconds: Mapped[int] = mapped_column(nullable=False, default=30)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="database_connections")


class LlmConfiguration(Base):
    """
    A configured LLM provider/model pair available to a tenant. Exactly one
    row per tenant should have `is_currently_active=True` at any time -- the
    application layer (LLMAdapterFactory) enforces this invariant when an
    operator hot-swaps providers via the Admin Panel.
    """

    __tablename__ = "llm_configurations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)

    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # 'openai' | 'anthropic' | 'gemini' | 'ollama'
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    base_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)  # required for Ollama

    # Ciphertext only, nullable because Ollama configurations require no API key.
    encrypted_api_key: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    is_currently_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    tenant: Mapped["Tenant"] = relationship(back_populates="llm_configurations")


class QueryAuditLog(Base):
    """
    Immutable audit trail of every natural-language question, the SQL that
    was ultimately executed, and the outcome. Required for the security
    posture described in the architectural analysis -- never delete rows
    from this table from application code; retention/deletion policy should
    be handled exclusively via a separate, explicitly reviewed migration.
    """

    __tablename__ = "query_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=_new_uuid)
    tenant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tenants.id"), nullable=False)
    admin_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    natural_language_question: Mapped[str] = mapped_column(Text, nullable=False)
    final_executed_sql: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    was_successful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    self_correction_attempts: Mapped[int] = mapped_column(nullable=False, default=0)

    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
