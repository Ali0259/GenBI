# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/) —
`MAJOR.MINOR.PATCH`:
- **MAJOR** — incompatible schema or API changes that require manual intervention to upgrade.
- **MINOR** — new functionality, backward-compatible.
- **PATCH** — backward-compatible bug fixes only.

## [Unreleased]

Nothing yet.

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
