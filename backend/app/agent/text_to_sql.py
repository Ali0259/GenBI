"""
app.agent.text_to_sql
-----------------------
The core GenBI reasoning loop: natural language question -> validated,
safe, executable SQL -> result set.

Two safety-critical components live here:

1. SafetySandbox: parses every LLM-generated SQL statement into an AST
   (via sqlglot) for the target dialect and rejects anything that is not
   a pure read (SELECT / WITH ... SELECT) statement. This is the
   authoritative gate -- prompt instructions to the LLM are a courtesy,
   not a control.

2. TextToSqlAgent: orchestrates generate -> sandbox-check -> execute,
   and on execution failure (syntax error, unknown column, etc.) feeds the
   exact database error back to the active LLM adapter for up to 3
   self-correction attempts before giving up.

Execution itself always happens against a connection authenticated as the
tenant's dedicated read-only database role (enforced upstream when the
Engine is constructed -- see app.models.admin_models for where that
connection string is stored).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import sqlglot
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.db.introspection import SqlDialect
from app.llm.base import BaseLLMAdapter, LLMAdapterError

# Statement types that must NEVER reach a target database connection,
# regardless of what the LLM produced. This list is intentionally explicit
# rather than an "allow SELECT, block nothing else" default -- fail closed.
#
# Built defensively via getattr rather than direct attribute access: exact
# class names in sqlglot.expressions have changed between versions (e.g.
# some releases expose ALTER TABLE as `AlterTable`, others under a
# differently named class), and a direct `sqlglot.exp.SomeName` reference
# that doesn't exist in the currently installed version raises
# AttributeError at IMPORT time -- which takes down the entire application,
# not just SQL validation, exactly as happened in production once before.
# Getattr-with-default means a name that doesn't exist in this version is
# silently skipped here rather than crashing the whole backend.
#
# This is safe to do because it is not the only defense: SafetySandbox's
# root-statement-type check (only SELECT / WITH / UNION allowed as the
# root node, done separately below) already blocks every top-level
# INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/etc. regardless of whether that
# exact class also appears in this list. This list exists purely to catch
# such statements nested inside a CTE or subquery, which is a smaller
# additional net, not the primary safety boundary.
_FORBIDDEN_STATEMENT_TYPE_CANDIDATE_NAMES: tuple[str, ...] = (
    "Insert",
    "Update",
    "Delete",
    "Drop", "DropTable",
    "Alter", "AlterTable", "AlterColumn", "AddConstraint", "RenameTable",
    "Create", "CreateTable",
    "TruncateTable", "Truncate",
    "Merge",
    "Command",  # catches EXEC/EXECUTE and other vendor procedural statements
    "Grant", "Revoke",
)
_FORBIDDEN_STATEMENT_TYPES: tuple[type, ...] = tuple(
    getattr(sqlglot.exp, name)
    for name in _FORBIDDEN_STATEMENT_TYPE_CANDIDATE_NAMES
    if hasattr(sqlglot.exp, name)
)

_DIALECT_TO_SQLGLOT_NAME: dict[SqlDialect, str] = {
    SqlDialect.MSSQL: "tsql",
    SqlDialect.POSTGRESQL: "postgres",
    SqlDialect.MYSQL: "mysql",
    SqlDialect.MARIADB: "mysql",
}


class SqlSafetyViolation(Exception):
    """Raised by SafetySandbox when generated SQL fails the read-only parse gate."""


class TextToSqlExhaustedError(Exception):
    """Raised when the self-correction loop exhausts all allowed repair attempts."""


class SafetySandbox:
    """
    Parses and validates that a SQL string is a single, pure read statement
    for the given dialect. Stateless and reusable across requests.
    """

    @staticmethod
    def validate(sql_text: str, dialect: SqlDialect) -> str:
        """
        Returns the validated SQL string (trimmed of any trailing semicolon
        and whitespace) if it passes all checks. Raises SqlSafetyViolation
        otherwise. Never mutates the SQL's semantics -- only trims formatting.
        """
        sqlglot_dialect_name = _DIALECT_TO_SQLGLOT_NAME.get(dialect)
        if sqlglot_dialect_name is None:
            raise SqlSafetyViolation(f"No sqlglot dialect mapping registered for '{dialect.value}'.")

        cleaned_sql = sql_text.strip().rstrip(";").strip()
        if not cleaned_sql:
            raise SqlSafetyViolation("Generated SQL was empty after cleaning.")

        try:
            parsed_statements = sqlglot.parse(cleaned_sql, read=sqlglot_dialect_name)
        except sqlglot.errors.ParseError as exc:
            raise SqlSafetyViolation(f"Generated SQL failed to parse for dialect '{dialect.value}': {exc}") from exc

        if parsed_statements is None or len(parsed_statements) == 0:
            raise SqlSafetyViolation("SQL parser returned no statements.")

        if len(parsed_statements) > 1:
            raise SqlSafetyViolation(
                "Multiple SQL statements detected in a single generation. "
                "Only one statement is permitted per query (blocks stacked-query injection)."
            )

        root_expression = parsed_statements[0]
        if root_expression is None:
            raise SqlSafetyViolation("Parsed SQL produced an empty expression tree.")

        if not isinstance(root_expression, (sqlglot.exp.Select, sqlglot.exp.Union, sqlglot.exp.With)):
            raise SqlSafetyViolation(
                f"Root statement type '{type(root_expression).__name__}' is not a permitted "
                f"read-only statement. Only SELECT / WITH / UNION are allowed."
            )

        for forbidden_type in _FORBIDDEN_STATEMENT_TYPES:
            if list(root_expression.find_all(forbidden_type)):
                raise SqlSafetyViolation(
                    f"Generated SQL contains a forbidden nested statement of type "
                    f"'{forbidden_type.__name__}'. Only read-only queries are permitted."
                )

        return cleaned_sql


@dataclass
class TextToSqlAttempt:
    """Record of a single generation/repair attempt, kept for audit logging."""

    attempt_number: int
    sql_text: str
    succeeded: bool
    error_message: Optional[str] = None


@dataclass
class TextToSqlResult:
    """Final outcome of a text-to-SQL run, including the full audit trail."""

    final_sql: str
    row_results: list[dict]
    attempts: list[TextToSqlAttempt] = field(default_factory=list)


class TextToSqlAgent:
    """
    Orchestrates the full generate -> validate -> execute -> self-correct
    loop for a single natural language question against a single tenant's
    target database connection.
    """

    MAX_SELF_CORRECTION_ATTEMPTS = 3
    MAX_RESULT_ROWS = 500

    def __init__(self, llm_adapter: BaseLLMAdapter, target_engine: Engine, dialect: SqlDialect,
                 schema_context: str) -> None:
        self._llm_adapter = llm_adapter
        self._target_engine = target_engine
        self._dialect = dialect
        self._schema_context = schema_context

    def _execute_validated_sql(self, validated_sql: str) -> list[dict]:
        """
        Executes an already-validated SQL string against the target engine.
        Assumes the engine's connection is already scoped to a read-only
        database role -- this method performs no additional permission
        checks itself, it relies on the caller and on the database's own
        GRANT configuration as the final line of defense.
        """
        try:
            with self._target_engine.connect() as connection:
                cursor_result = connection.execute(text(validated_sql))
                fetched_rows = cursor_result.mappings().fetchmany(self.MAX_RESULT_ROWS)
                return [dict(row) for row in fetched_rows]
        except SQLAlchemyError as exc:
            # Re-raise as a plain string-carrying exception so the caller can
            # feed the exact driver error text back into the LLM repair prompt.
            raise RuntimeError(str(exc.orig) if getattr(exc, "orig", None) else str(exc)) from exc

    async def run(self, natural_language_question: str) -> TextToSqlResult:
        """
        Executes the full loop: initial generation, safety validation,
        execution, and up to MAX_SELF_CORRECTION_ATTEMPTS repair attempts
        if execution fails. Raises TextToSqlExhaustedError if every attempt
        is exhausted without producing a successful execution.
        """
        attempts: list[TextToSqlAttempt] = []

        try:
            completion = await self._llm_adapter.generate_sql(
                natural_language_question=natural_language_question,
                schema_context=self._schema_context,
                sql_dialect=self._dialect.value,
            )
        except LLMAdapterError as exc:
            raise TextToSqlExhaustedError(f"LLM generation failed before any attempt could be made: {exc}") from exc

        candidate_sql = completion.text

        for attempt_number in range(1, self.MAX_SELF_CORRECTION_ATTEMPTS + 1):
            try:
                validated_sql = SafetySandbox.validate(candidate_sql, self._dialect)
            except SqlSafetyViolation as safety_error:
                attempts.append(TextToSqlAttempt(attempt_number, candidate_sql, False, str(safety_error)))
                # A safety violation is NOT retried via self-correction with the
                # same "fix the syntax" prompt -- we deliberately stop here rather
                # than coach the model toward bypassing the safety gate.
                raise TextToSqlExhaustedError(
                    f"Generated SQL failed the safety sandbox on attempt {attempt_number} "
                    f"and was not retried: {safety_error}"
                ) from safety_error

            try:
                row_results = self._execute_validated_sql(validated_sql)
                attempts.append(TextToSqlAttempt(attempt_number, validated_sql, True))
                return TextToSqlResult(final_sql=validated_sql, row_results=row_results, attempts=attempts)
            except RuntimeError as execution_error:
                error_message = str(execution_error)
                attempts.append(TextToSqlAttempt(attempt_number, validated_sql, False, error_message))

                if attempt_number == self.MAX_SELF_CORRECTION_ATTEMPTS:
                    break

                try:
                    repair_completion = await self._llm_adapter.repair_sql(
                        failed_sql=validated_sql,
                        database_error_message=error_message,
                        schema_context=self._schema_context,
                        sql_dialect=self._dialect.value,
                    )
                except LLMAdapterError as repair_llm_error:
                    raise TextToSqlExhaustedError(
                        f"LLM self-correction call failed on attempt {attempt_number}: {repair_llm_error}"
                    ) from repair_llm_error

                candidate_sql = repair_completion.text

        raise TextToSqlExhaustedError(
            f"Exhausted all {self.MAX_SELF_CORRECTION_ATTEMPTS} self-correction attempts. "
            f"Last error: {attempts[-1].error_message if attempts else 'unknown'}"
        )
