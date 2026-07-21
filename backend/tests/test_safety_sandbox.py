"""
tests.test_safety_sandbox
---------------------------
Unit tests for app.agent.text_to_sql.SafetySandbox -- the single most
safety-critical component in the platform. These tests should be run in CI
on every commit (see .github/workflows/ci.yml) and must never be weakened
to make a feature "work" -- if a legitimate query fails one of these tests,
the query needs to change, not the sandbox.
"""

from __future__ import annotations

import pytest

from app.agent.text_to_sql import SafetySandbox, SqlSafetyViolation
from app.db.introspection import SqlDialect


class TestForbiddenStatementTypesListIsPopulated:
    def test_forbidden_statement_types_is_not_empty(self) -> None:
        """
        Regression test for a real incident: _FORBIDDEN_STATEMENT_TYPES is
        built defensively via getattr so a class name missing in a given
        sqlglot version doesn't crash the whole app at import time (see
        CHANGELOG). This test guards the other failure direction -- making
        sure that defensiveness never silently degrades into an EMPTY list,
        which would quietly remove the nested-DML defense-in-depth layer
        entirely without anyone noticing.
        """
        from app.agent.text_to_sql import _FORBIDDEN_STATEMENT_TYPES

        assert len(_FORBIDDEN_STATEMENT_TYPES) > 0, (
            "No forbidden statement types resolved against the installed sqlglot version -- "
            "check _FORBIDDEN_STATEMENT_TYPE_CANDIDATE_NAMES against the current sqlglot.expressions API."
        )


class TestSafetySandboxAcceptsReadOnlyQueries:
    def test_accepts_simple_select_postgres(self) -> None:
        sql = "SELECT id, name FROM customers LIMIT 100"
        result = SafetySandbox.validate(sql, SqlDialect.POSTGRESQL)
        assert result.upper().startswith("SELECT")

    def test_accepts_select_with_top_mssql(self) -> None:
        sql = "SELECT TOP 100 id, name FROM customers"
        result = SafetySandbox.validate(sql, SqlDialect.MSSQL)
        assert "TOP" in result.upper()

    def test_accepts_cte_with_statement(self) -> None:
        sql = "WITH recent AS (SELECT id FROM orders WHERE created_at > '2026-01-01') SELECT * FROM recent LIMIT 50"
        result = SafetySandbox.validate(sql, SqlDialect.POSTGRESQL)
        assert result.upper().startswith("WITH")

    def test_strips_trailing_semicolon(self) -> None:
        sql = "SELECT id FROM customers LIMIT 10;"
        result = SafetySandbox.validate(sql, SqlDialect.MYSQL)
        assert not result.endswith(";")


class TestSafetySandboxRejectsMutations:
    @pytest.mark.parametrize(
        "forbidden_sql",
        [
            "DELETE FROM customers WHERE id = 1",
            "UPDATE customers SET name = 'x' WHERE id = 1",
            "INSERT INTO customers (id, name) VALUES (1, 'x')",
            "DROP TABLE customers",
            "TRUNCATE TABLE customers",
            "ALTER TABLE customers ADD COLUMN x INT",
        ],
    )
    def test_rejects_dml_and_ddl(self, forbidden_sql: str) -> None:
        with pytest.raises(SqlSafetyViolation):
            SafetySandbox.validate(forbidden_sql, SqlDialect.POSTGRESQL)

    def test_rejects_stacked_queries(self) -> None:
        sql = "SELECT id FROM customers; DELETE FROM customers"
        with pytest.raises(SqlSafetyViolation):
            SafetySandbox.validate(sql, SqlDialect.POSTGRESQL)

    def test_rejects_empty_sql(self) -> None:
        with pytest.raises(SqlSafetyViolation):
            SafetySandbox.validate("   ", SqlDialect.POSTGRESQL)

    def test_rejects_unparseable_sql(self) -> None:
        with pytest.raises(SqlSafetyViolation):
            SafetySandbox.validate("SELEKT garbled nonsense (((", SqlDialect.POSTGRESQL)
