"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-18 00:00:00

This is the ONLY migration allowed to create these tables outright. Every
future schema change must be a NEW migration file that ALTERs these tables
additively (nullable columns first, tightened in a later release) -- never
edit this file after it has shipped in a release, per the data-migration
safety principles in the project README.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_initial_schema"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "admin_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("is_superadmin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("email", name="uq_admin_users_email"),
    )

    op.create_table(
        "target_database_connections",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("dialect", sa.String(length=32), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("database_name", sa.String(length=255), nullable=False),
        sa.Column("read_only_username", sa.String(length=255), nullable=False),
        sa.Column("encrypted_connection_secret", sa.Text(), nullable=False),
        sa.Column("connect_timeout_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("statement_timeout_seconds", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "llm_configurations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=True),
        sa.Column("encrypted_api_key", sa.Text(), nullable=True),
        sa.Column("is_currently_active", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "query_audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("tenants.id"), nullable=False),
        sa.Column("admin_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("natural_language_question", sa.Text(), nullable=False),
        sa.Column("final_executed_sql", sa.Text(), nullable=True),
        sa.Column("was_successful", sa.Boolean(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("self_correction_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_index("ix_admin_users_tenant_id", "admin_users", ["tenant_id"])
    op.create_index("ix_target_database_connections_tenant_id", "target_database_connections", ["tenant_id"])
    op.create_index("ix_llm_configurations_tenant_id", "llm_configurations", ["tenant_id"])
    op.create_index("ix_query_audit_logs_tenant_id", "query_audit_logs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_query_audit_logs_tenant_id", table_name="query_audit_logs")
    op.drop_index("ix_llm_configurations_tenant_id", table_name="llm_configurations")
    op.drop_index("ix_target_database_connections_tenant_id", table_name="target_database_connections")
    op.drop_index("ix_admin_users_tenant_id", table_name="admin_users")

    op.drop_table("query_audit_logs")
    op.drop_table("llm_configurations")
    op.drop_table("target_database_connections")
    op.drop_table("admin_users")
    op.drop_table("tenants")
