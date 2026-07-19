# GenBI Platform

An open-source, multi-tenant Generative Business Intelligence (GenBI) platform.
Business users ask questions in plain language through a conversational **OpenUI**
frontend; a separate, password-protected **Admin Panel** handles tenant setup,
target-database connections, and LLM provider configuration.

The backend generates SQL against your existing databases using a hot-swappable
LLM provider (OpenAI, Anthropic, Gemini, or a fully offline Ollama instance),
validates every generated statement through an AST-level read-only safety gate
before it ever touches your data, and self-corrects up to three times if a
query fails to execute.

## Architecture

```
                     ┌────────────────────┐
                     │   Reverse Proxy     │  (Traefik, TLS termination,
                     │   (Traefik)         │   host-based routing)
                     └──────────┬─────────┘
                 ┌──────────────┼──────────────┐
                 │                              │
        ┌────────▼─────────┐          ┌────────▼──────────┐
        │  OpenUI Frontend  │          │  Admin Panel        │
        │  (business users) │          │  Frontend (operators)│
        └────────┬─────────┘          └────────┬──────────┘
                 │                              │
                 └──────────────┬───────────────┘
                                │
                       ┌────────▼─────────┐
                       │   Backend API     │  FastAPI, Python 3.12
                       │  (this repo's     │
                       │   `backend/`)     │
                       └───┬───────────┬───┘
                           │           │
              ┌────────────▼───┐   ┌───▼─────────────────┐
              │ Admin Database  │   │  Target Databases    │
              │ (Postgres,      │   │  (MSSQL / Postgres /  │
              │  metadata only) │   │   MySQL / MariaDB,     │
              │                 │   │   read-only role)      │
              └─────────────────┘   └────────────────────────┘
```

## Repository layout

```
.
├── install.sh                  # Foolproof, idempotent installer (Ubuntu LTS)
├── docker-compose.yml          # Orchestrates every service
├── .env.example                # Documents every required environment variable
├── backend/                    # FastAPI application
│   ├── app/
│   │   ├── llm/                # BaseLLMAdapter + OpenAI/Anthropic/Gemini/Ollama adapters + hot-swap factory
│   │   ├── db/                 # Dialect-aware schema introspection + dynamic target-DB engine builder
│   │   ├── agent/               # Text-to-SQL agent: safety sandbox + self-correction loop
│   │   ├── models/              # SQLAlchemy ORM models for the admin database (encrypted credentials)
│   │   ├── schemas/              # Pydantic API request/response schemas
│   │   ├── api/                  # FastAPI routers (auth, admin, query)
│   │   ├── config.py, database.py, security.py, main.py
│   ├── alembic/                # Schema migrations for the admin database ONLY
│   ├── tests/                  # Unit tests (safety sandbox is the priority target)
│   ├── Dockerfile
│   └── requirements.txt
├── frontend-openui/            # Business-user chat interface (React + Vite)
├── frontend-admin/             # Password-protected admin panel (React + Vite)
└── .github/workflows/ci.yml    # Tests + Docker build validation on every PR
```

## Quick start (production, Ubuntu 20.04/22.04/24.04 LTS)

```bash
git clone https://github.com/your-org/genbi-platform.git
cd genbi-platform
sudo ./install.sh
```

`install.sh` will:
1. Verify you're on a supported Ubuntu LTS release with outbound network access.
2. Install Docker Engine and the Compose plugin (skipped if already present).
3. Generate a `.env` file with strong, random secrets (skipped if one already exists — **it will never overwrite existing secrets**).
4. Build every application image from source and start the full stack.

By default the OpenUI is served at `http://app.localhost` and the Admin Panel
at `http://admin.localhost`. Point real DNS records at your server and update
`GENBI_OPENUI_DOMAIN` / `GENBI_ADMIN_DOMAIN` in `.env`, then run:

```bash
docker compose up -d
```

to apply the new domains.

## Local development

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp ../.env.example ../.env   # fill in real local values
alembic upgrade head
uvicorn app.main:app --reload

# OpenUI frontend
cd frontend-openui
npm install
npm run dev   # http://localhost:5173

# Admin frontend
cd frontend-admin
npm install
npm run dev   # http://localhost:5174 (see vite.config.js)
```

Run backend tests with:

```bash
cd backend
pytest -v
```

## Security model (read before connecting a production database)

1. **Every target database connection must authenticate as a dedicated,
   read-only role** — granted `SELECT` only, no `INSERT/UPDATE/DELETE/DDL`,
   no stored-procedure execution. This is enforced by the database engine
   itself; the application never assumes it's the only safeguard.
2. **Every LLM-generated SQL statement passes an AST-level safety gate**
   (`app/agent/text_to_sql.py::SafetySandbox`, built on `sqlglot`) before
   execution. Only `SELECT` / `WITH` / `UNION` root statements are allowed;
   anything containing nested DML/DDL, multiple statements, or a parse
   failure is rejected outright — no self-correction retry on a safety
   rejection, since coaching a model past a safety filter is exactly the
   wrong instinct.
3. **Target-database credentials and LLM API keys are never stored in
   plaintext.** They're encrypted with a Fernet key that lives only in an
   environment variable (`GENBI_MASTER_ENCRYPTION_KEY`), never in the
   database itself — a stolen backup is useless without it.
4. **Every query is audit-logged** (`QueryAuditLog`), including the
   question, the final executed SQL, and every self-correction attempt.
5. **Connections to target databases use `NullPool`** (short-lived,
   per-query, always closed) rather than a persistent pool, and a
   statement timeout is enforced at the database session level in addition
   to the safety sandbox.

## Data migration safety

Schema changes to the **admin database** are managed exclusively through
Alembic (`backend/alembic/`). The container entrypoint
(`backend/entrypoint.sh`) takes a `pg_dump` snapshot before every
`alembic upgrade head`, and refuses to start the API server if the migration
fails. Never edit a migration file that has already shipped — add a new one.
The encryption key that protects stored credentials is never part of the
database or its backups, so key rotation is a separate, explicit operation
and never a migration concern.

## Updating an existing installation

```bash
git pull
docker compose build
docker compose up -d
```

Alembic migrations run automatically on backend container startup, after a
pre-migration snapshot is taken.

## License

MIT — see [LICENSE](LICENSE).
