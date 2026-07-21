# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/) —
`MAJOR.MINOR.PATCH`:
- **MAJOR** — incompatible schema or API changes that require manual intervention to upgrade.
- **MINOR** — new functionality, backward-compatible.
- **PATCH** — backward-compatible bug fixes only.

## [Unreleased]

### Added
- Self-service "change my password" endpoint (`POST /api/auth/change-password`)
  and a matching panel in the Admin Panel UI.
- `app.scripts.reset_admin_password` CLI for resetting a password without
  needing the current one (lockout recovery).
- `uninstall.sh` with partial (keep data) and complete (wipe everything) removal modes.

### Fixed
- `install.sh` now detects a pre-existing admin database volume before
  generating a new `.env`, preventing a password mismatch between a fresh
  `.env` and a Postgres volume initialized under old credentials.
- `entrypoint.sh` now recognizes a password-authentication failure during
  migration and prints a specific, actionable hint instead of just failing.
- Fixed a FastAPI startup crash: the `/api/auth/change-password` route
  used `status_code=204` without `response_model=None`, which raises an
  `AssertionError` at import time and silently took down the entire
  backend (every request refused, no crash-loop visible in `docker ps`
  status). Added `tests/test_app_imports.py`, which actually imports the
  FastAPI app in CI, so an import-time error like this fails the build
  instead of shipping.
- Silenced a Pydantic warning about `model_name` colliding with its
  reserved `model_` namespace on the LLM configuration schemas.
- Fixed another FastAPI startup crash: `app.agent.text_to_sql` referenced
  `sqlglot.exp.AlterTable` directly, which doesn't exist in the pinned
  `sqlglot==25.24.5` release and raised `AttributeError` at import time
  (same silent-crash symptom as the 204 route bug above). The forbidden
  nested-statement-type list is now built defensively via `getattr`, so a
  class name that doesn't exist in a given `sqlglot` version is skipped
  rather than crashing the app -- the primary safety boundary (root
  statement must be SELECT/WITH/UNION) was never affected by this bug and
  remained in force throughout.

## [1.0.0] - 2026-07-20

### Added
- Initial release of the GenBI platform.
- Split architecture: OpenUI (business-user chat interface) and a
  password-protected Admin Panel, routed by port (80 / 8080) so a fresh
  install works via bare IP with no DNS configuration required.
- Multi-LLM adapter layer with runtime hot-swapping: OpenAI, Anthropic,
  Gemini, and local/offline Ollama.
- Dialect-aware schema introspection for MSSQL, PostgreSQL, MySQL, and MariaDB.
- Text-to-SQL agent with an AST-level read-only safety sandbox (`sqlglot`)
  and a self-correction loop (up to 3 attempts) on execution failure.
- Encrypted-at-rest storage of target-database credentials and LLM API
  keys (Fernet, master key held outside the database).
- Automatic default-admin bootstrap on first install (idempotent — never
  runs again once any admin user exists).
- Alembic-managed schema migrations for the admin database, with an
  automatic pre-migration `pg_dump` snapshot on every container start.
- Foolproof `install.sh`: installs Docker, generates secrets, builds and
  starts the full stack, provisions the first login.
