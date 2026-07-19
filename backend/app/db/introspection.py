"""
app.db.introspection
---------------------
Produces a normalized, LLM-ready textual schema description for whichever
target database dialect is currently active for a tenant's connection.

MSSQL, PostgreSQL, and MySQL/MariaDB all expose an INFORMATION_SCHEMA, but
the queries needed to get a clean, useful result differ enough (row-limiting
syntax, catalog filtering, identifier quoting) that a single shared query
string is not reliable across all three. This module keeps one query per
dialect and normalizes the output shape.

This function must ONLY ever be executed against the tenant's dedicated
READ-ONLY database role (see architectural note in the accompanying
critical analysis). It performs no writes and accepts no user-supplied
SQL fragments.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


class SqlDialect(str, Enum):
    MSSQL = "mssql"
    POSTGRESQL = "postgresql"
    MYSQL = "mysql"
    MARIADB = "mariadb"


class SchemaIntrospectionError(Exception):
    """Raised when the schema of a target database cannot be read."""


@dataclass(frozen=True)
class ColumnMetadata:
    table_schema: str
    table_name: str
    column_name: str
    data_type: str
    is_nullable: bool


# --------------------------------------------------------------------------
# Dialect-specific INFORMATION_SCHEMA queries.
#
# Notes:
# - MySQL and MariaDB share identical INFORMATION_SCHEMA semantics for this
#   purpose, so they use the same query string.
# - We explicitly exclude system schemas (pg_catalog, information_schema,
#   sys, mysql, performance_schema) so the LLM is never handed irrelevant
#   internal metadata tables that could confuse SQL generation.
# --------------------------------------------------------------------------

_QUERY_BY_DIALECT: dict[SqlDialect, str] = {
    SqlDialect.MSSQL: """
        SELECT
            TABLE_SCHEMA   AS table_schema,
            TABLE_NAME     AS table_name,
            COLUMN_NAME    AS column_name,
            DATA_TYPE      AS data_type,
            IS_NULLABLE    AS is_nullable
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA')
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
    """,
    SqlDialect.POSTGRESQL: """
        SELECT
            table_schema,
            table_name,
            column_name,
            data_type,
            is_nullable
        FROM information_schema.columns
        WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
        ORDER BY table_schema, table_name, ordinal_position
    """,
    SqlDialect.MYSQL: """
        SELECT
            TABLE_SCHEMA   AS table_schema,
            TABLE_NAME     AS table_name,
            COLUMN_NAME    AS column_name,
            DATA_TYPE      AS data_type,
            IS_NULLABLE    AS is_nullable
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA NOT IN ('information_schema', 'mysql', 'performance_schema', 'sys')
        ORDER BY TABLE_SCHEMA, TABLE_NAME, ORDINAL_POSITION
    """,
}
_QUERY_BY_DIALECT[SqlDialect.MARIADB] = _QUERY_BY_DIALECT[SqlDialect.MYSQL]


def fetch_schema_columns(engine: Engine, dialect: SqlDialect,
                          max_tables: int = 200) -> list[ColumnMetadata]:
    """
    Executes the dialect-appropriate INFORMATION_SCHEMA query and returns a
    normalized list of ColumnMetadata. Raises SchemaIntrospectionError on any
    connection or query failure -- callers should surface this as a clear
    "cannot reach target database" error rather than letting the LLM attempt
    to generate SQL against an empty/unknown schema.

    `max_tables` acts as a defensive cap: extremely large legacy databases
    (thousands of tables) would blow the LLM context window if dumped
    wholesale. Truncating here is a stopgap; the production path is the
    semantic-layer / embedding-retrieval approach discussed in the
    architectural analysis.
    """
    query_string = _QUERY_BY_DIALECT.get(dialect)
    if query_string is None:
        raise SchemaIntrospectionError(f"No introspection query registered for dialect '{dialect.value}'.")

    try:
        with engine.connect() as connection:
            result_rows = connection.execute(text(query_string)).mappings().all()
    except SQLAlchemyError as exc:
        raise SchemaIntrospectionError(
            f"Failed to introspect schema for dialect '{dialect.value}': {exc}"
        ) from exc

    columns: list[ColumnMetadata] = []
    seen_tables: set[tuple[str, str]] = set()

    for row in result_rows:
        table_key = (str(row["table_schema"]), str(row["table_name"]))
        if table_key not in seen_tables:
            if len(seen_tables) >= max_tables:
                break
            seen_tables.add(table_key)

        if table_key in seen_tables:
            columns.append(
                ColumnMetadata(
                    table_schema=str(row["table_schema"]),
                    table_name=str(row["table_name"]),
                    column_name=str(row["column_name"]),
                    data_type=str(row["data_type"]),
                    is_nullable=str(row["is_nullable"]).upper() in ("YES", "TRUE", "1"),
                )
            )

    return columns


def render_schema_as_llm_context(columns: list[ColumnMetadata]) -> str:
    """
    Converts a flat list of ColumnMetadata into a compact, grouped textual
    representation suitable for insertion into an LLM prompt, e.g.:

        schema.customers (id int NOT NULL, email varchar NULL, ...)
        schema.orders (id int NOT NULL, customer_id int NOT NULL, ...)
    """
    if not columns:
        raise SchemaIntrospectionError(
            "Schema introspection returned zero columns. The target database "
            "may be empty, or the connected role lacks INFORMATION_SCHEMA visibility."
        )

    grouped: dict[tuple[str, str], list[ColumnMetadata]] = {}
    for column in columns:
        key = (column.table_schema, column.table_name)
        grouped.setdefault(key, []).append(column)

    lines: list[str] = []
    for (schema_name, table_name), table_columns in grouped.items():
        column_descriptions = ", ".join(
            f"{c.column_name} {c.data_type} {'NULL' if c.is_nullable else 'NOT NULL'}"
            for c in table_columns
        )
        lines.append(f"{schema_name}.{table_name} ({column_descriptions})")

    return "\n".join(lines)
