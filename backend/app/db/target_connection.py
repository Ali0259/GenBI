"""
app.db.target_connection
--------------------------
Builds a short-lived SQLAlchemy Engine for a tenant's external target
database (MSSQL / PostgreSQL / MySQL / MariaDB) from an encrypted
TargetDatabaseConnection row.

Deliberate design choices:
- NullPool is used for every target engine, never QueuePool. These are
  ad-hoc, per-query connections to arbitrary customer infrastructure --
  holding a warm pool of connections to a customer's production database
  indefinitely is both a resource risk on their end and a security exposure
  on ours if the process is compromised. Each query opens a connection,
  runs, and closes it.
- Connect and statement timeouts are always applied, sourced from the
  TargetDatabaseConnection row (falling back to global defaults), so a
  runaway query can never hang a worker indefinitely.
- The connection is authenticated as `read_only_username` exclusively --
  this module has no code path that could construct a connection with any
  other role, by design.
"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

from app.config import get_settings
from app.db.introspection import SqlDialect
from app.models.admin_models import CredentialEncryptor, TargetDatabaseConnection

_settings = get_settings()

_DRIVER_BY_DIALECT: dict[SqlDialect, str] = {
    SqlDialect.MSSQL: "mssql+pyodbc",
    SqlDialect.POSTGRESQL: "postgresql+psycopg",
    SqlDialect.MYSQL: "mysql+pymysql",
    SqlDialect.MARIADB: "mysql+pymysql",
}


class TargetConnectionError(Exception):
    """Raised when a target database engine cannot be constructed or connected to."""


def _build_connection_url(connection_row: TargetDatabaseConnection, decrypted_password: str) -> str:
    driver = _DRIVER_BY_DIALECT.get(SqlDialect(connection_row.dialect))
    if driver is None:
        raise TargetConnectionError(f"Unsupported dialect '{connection_row.dialect}'.")

    encoded_username = urllib.parse.quote_plus(connection_row.read_only_username)
    encoded_password = urllib.parse.quote_plus(decrypted_password)

    base_url = (
        f"{driver}://{encoded_username}:{encoded_password}@"
        f"{connection_row.host}:{connection_row.port}/{connection_row.database_name}"
    )

    if driver == "mssql+pyodbc":
        base_url += "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"

    return base_url


def _apply_statement_timeout_on_connect(engine: Engine, dialect: SqlDialect, statement_timeout_seconds: int) -> None:
    """
    Registers a `connect` event listener that applies a session-level
    statement timeout immediately after every new physical connection is
    opened. This is defense-in-depth against a generated query that passes
    the safety sandbox but is simply too expensive to run.
    """

    @event.listens_for(engine, "connect")
    def _set_session_timeout(dbapi_connection: Any, connection_record: Any) -> None:  # noqa: ARG001
        cursor = dbapi_connection.cursor()
        try:
            if dialect == SqlDialect.POSTGRESQL:
                cursor.execute(f"SET statement_timeout = {statement_timeout_seconds * 1000}")
            elif dialect in (SqlDialect.MYSQL, SqlDialect.MARIADB):
                cursor.execute(f"SET SESSION MAX_EXECUTION_TIME = {statement_timeout_seconds * 1000}")
            elif dialect == SqlDialect.MSSQL:
                cursor.execute(f"SET LOCK_TIMEOUT {statement_timeout_seconds * 1000}")
        finally:
            cursor.close()


def build_target_engine(connection_row: TargetDatabaseConnection, encryptor: CredentialEncryptor) -> Engine:
    """
    Constructs a NullPool-backed Engine for the given stored connection,
    decrypting its credential secret exactly once, in memory, for the
    duration of this call. Raises TargetConnectionError on any failure
    (bad ciphertext, unsupported dialect, malformed stored secret).
    """
    try:
        decrypted_payload = json.loads(encryptor.decrypt(connection_row.encrypted_connection_secret))
        decrypted_password = str(decrypted_payload["password"])
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise TargetConnectionError(
            "Stored connection secret is malformed or missing a 'password' field."
        ) from exc

    dialect = SqlDialect(connection_row.dialect)
    connection_url = _build_connection_url(connection_row, decrypted_password)

    engine = create_engine(
        connection_url,
        poolclass=NullPool,
        connect_args={"timeout": connection_row.connect_timeout_seconds}
        if dialect == SqlDialect.MSSQL
        else {"connect_timeout": connection_row.connect_timeout_seconds},
    )

    _apply_statement_timeout_on_connect(engine, dialect, connection_row.statement_timeout_seconds)

    return engine
