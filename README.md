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
git clone https://github.com/ali0259/genbi.git
cd genbi
sudo ./install.sh
```

`install.sh` will:
1. Verify you're on a supported Ubuntu LTS release with outbound network access.
2. Install Docker Engine and the Compose plugin (skipped if already present).
3. Generate a `.env` file with strong, random secrets (skipped if one already exists — **it will never overwrite existing secrets**).
4. Build every application image from source and start the full stack.

By default:
- **OpenUI** (business users) is served on port **80**: `http://<your-server-ip-or-domain>/`
- **Admin Panel** (operators) is served on port **8080**: `http://<your-server-ip-or-domain>:8080/`

This works identically whether you connect via a bare IP address, `localhost`,
or a real domain — **no DNS or `/etc/hosts` configuration is required.**
Routing is done by port, not hostname, specifically so a fresh install works
immediately on a LAN server reached only by IP. If `80` or `8080` are already
in use on your host, set `GENBI_OPENUI_PORT` / `GENBI_ADMIN_PORT` in `.env`
to different values, then run:

```bash
docker compose up -d
```

to apply the new domains.

## Your first login

`install.sh` automatically creates a default tenant and a superadmin account
the very first time the platform starts with zero admin users. At the end
of installation you'll see credentials printed directly in the terminal,
and they're also saved to `secrets/admin_credentials.txt` (root-readable
only) for later reference:

```
GenBI Platform -- Default Admin Credentials
=============================================
Email:    admin@genbi.local
Password: <randomly generated>
```

Log in to the Admin Panel with those, then **change the password
immediately** (or create a personal admin account via the Admin Panel and
deactivate the default one). This step only ever runs once — on every
subsequent start or upgrade, the bootstrap script checks whether any admin
user already exists and does nothing if so, so it's safe across restarts
and version upgrades.

Want a different default email instead of `admin@genbi.local`? Set
`GENBI_DEFAULT_ADMIN_EMAIL` in `.env` **before** the first `docker compose up`.

To provision additional users (a second tenant, another admin), use:

```bash
docker compose exec backend_api python -m app.scripts.create_admin_user \
    --tenant-name "Another Company" \
    --email someone@example.com
```

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

## Alternative: hostname-based routing

The default setup above routes by **port** (80 for OpenUI, 8080 for Admin
Panel) specifically so it works with zero configuration via a bare IP
address. If you have real DNS and want each frontend on its own subdomain
with independent TLS certificates instead, swap the labels in
`docker-compose.yml`:

```yaml
# On backend_api, openui_frontend, admin_panel_frontend: replace the
# PathPrefix(`/`) / PathPrefix(`/api`) + entrypoints=openui|adminui rules
# with, e.g.:
- "traefik.http.routers.genbi-openui.rule=Host(`app.yourdomain.com`)"
- "traefik.http.routers.genbi-openui.entrypoints=websecure"
- "traefik.http.routers.genbi-openui.tls.certresolver=letsencrypt"
```

...and add a Let's Encrypt `certresolver` to the `reverse_proxy` service's
`command:` block. This is more setup than the port-based default, so it's
left as an opt-in change rather than the default — most self-hosted
installs are better served by connecting via IP on day one.

## Versioning & upgrades

This project follows [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`), tracked in three synced places: the root `VERSION`
file, `backend/VERSION` (baked into the backend image and served at
`GET /api/version`), and both frontends' `package.json`. Every release is
also recorded in [CHANGELOG.md](CHANGELOG.md).

**Check what's currently running:**
```bash
curl http://<server>/api/version
# {"version": "1.0.0"}
```
Both the OpenUI and Admin Panel headers also show the version they're
talking to, in small text next to the title — handy for confirming an
upgrade actually took effect.

**Cutting a new release** (maintainers): after merging changes and adding a
`CHANGELOG.md` entry under `[Unreleased]`, run:
```bash
./scripts/release.sh 1.1.0
```
This bumps every version reference in sync, commits, and creates an
annotated git tag. Review the commit, then:
```bash
git push origin main --tags
```
Pushing the tag triggers `.github/workflows/release.yml`, which builds and
publishes all three images to GitHub Container Registry, tagged with both
the exact version and `latest`.

**Upgrading an existing installation** to a new version:
```bash
git fetch --tags
git checkout v1.1.0        # or `git pull` if you track main directly
docker compose build
docker compose up -d
```
The backend's `entrypoint.sh` automatically takes a `pg_dump` snapshot of
the admin database, then runs `alembic upgrade head`, before starting the
server on every container start — so every upgrade is preceded by a
recoverable snapshot, and the container refuses to come up if a migration
fails partway. The default-admin bootstrap step is a no-op on every upgrade
once at least one admin user exists, so upgrading never touches existing
accounts or credentials.

**Rolling back**, if a release causes a problem:
```bash
git checkout v1.0.0
docker compose build
docker compose up -d
```
If the newer version's migration changed the schema in a way the older
code can't read, restore the pre-migration snapshot from
`/opt/genbi/backups` inside the `backend_api` container (or the named
volume `genbi_db_backups`) before starting the older version. This is why
migrations should stay additive within a MINOR release line (see
`backend/alembic/versions/0001_initial_schema.py`'s docstring) — reserve
breaking schema changes for a MAJOR version bump, called out clearly in
`CHANGELOG.md`.

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
