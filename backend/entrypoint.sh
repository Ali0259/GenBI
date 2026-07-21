#!/usr/bin/env bash
#
# entrypoint.sh - runs inside the backend_api container on every start.
#
# Order of operations, matching the data-migration-safety design in the
# project README:
#   1. Take a timestamped pg_dump snapshot of the admin database BEFORE
#      touching its schema.
#   2. Run `alembic upgrade head`. If this fails, the container exits
#      non-zero rather than starting a server against a half-migrated schema.
#   3. Start uvicorn.

set -Eeuo pipefail

readonly BACKUP_DIR="/opt/genbi/backups"
readonly TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
readonly BACKUP_FILE="${BACKUP_DIR}/admin_db_pre_migration_${TIMESTAMP}.sql"

echo "[entrypoint] Ensuring backup directory exists at ${BACKUP_DIR}..."
mkdir -p "${BACKUP_DIR}"

echo "[entrypoint] Parsing ADMIN_DATABASE_URL for pg_dump connection details..."
# ADMIN_DATABASE_URL is of the form: postgresql+psycopg://user:password@host:port/dbname
DB_URL_STRIPPED="${ADMIN_DATABASE_URL#postgresql+psycopg://}"
DB_USER="${DB_URL_STRIPPED%%:*}"
DB_URL_REMAINDER="${DB_URL_STRIPPED#*:}"
DB_PASSWORD="${DB_URL_REMAINDER%%@*}"
DB_URL_REMAINDER="${DB_URL_REMAINDER#*@}"
DB_HOST="${DB_URL_REMAINDER%%:*}"
DB_URL_REMAINDER="${DB_URL_REMAINDER#*:}"
DB_PORT="${DB_URL_REMAINDER%%/*}"
DB_NAME="${DB_URL_REMAINDER#*/}"

echo "[entrypoint] Taking pre-migration snapshot of database '${DB_NAME}'..."
if PGPASSWORD="${DB_PASSWORD}" pg_dump -h "${DB_HOST}" -p "${DB_PORT}" -U "${DB_USER}" -d "${DB_NAME}" \
    --format=plain --no-owner --file="${BACKUP_FILE}"; then
    echo "[entrypoint] Snapshot saved to ${BACKUP_FILE}."
else
    echo "[entrypoint] WARNING: pre-migration snapshot failed. This is expected on a brand-new, " \
         "not-yet-created database (first-ever install). Proceeding with migration."
fi

echo "[entrypoint] Running Alembic migrations..."
alembic_output="$(alembic upgrade head 2>&1)" && alembic_exit_code=0 || alembic_exit_code=$?
echo "${alembic_output}"

if [[ "${alembic_exit_code}" -ne 0 ]]; then
    if echo "${alembic_output}" | grep -qi "password authentication failed"; then
        echo ""
        echo "[entrypoint] HINT: this looks like a credential mismatch, not a migration problem."
        echo "[entrypoint] Postgres only applies POSTGRES_PASSWORD the FIRST time it initializes an empty"
        echo "[entrypoint] data volume -- it will NOT reset the password on later starts. If your .env's"
        echo "[entrypoint] ADMIN_DB_PASSWORD was regenerated (e.g. .env was deleted and install.sh ran again)"
        echo "[entrypoint] while the admin database volume from a previous install still exists, the two"
        echo "[entrypoint] will no longer match. Fix without losing data by syncing Postgres's stored"
        echo "[entrypoint] password to your current .env from the HOST (not inside this container):"
        echo "[entrypoint]     NEW_PW=\$(grep -E '^ADMIN_DB_PASSWORD=' .env | cut -d= -f2)"
        echo "[entrypoint]     docker compose exec admin_database psql -U \${ADMIN_DB_USER:-genbi_admin_user} -d \${ADMIN_DB_NAME:-genbi_admin} -c \"ALTER USER \${ADMIN_DB_USER:-genbi_admin_user} WITH PASSWORD '\${NEW_PW}';\""
        echo "[entrypoint]     docker compose up -d backend_api"
        echo ""
    fi
    echo "[entrypoint] FATAL: alembic upgrade head failed. Refusing to start the API server " \
         "against a potentially half-migrated schema. A snapshot (if one was possible) is at ${BACKUP_FILE}."
    exit 1
fi
echo "[entrypoint] Migrations applied successfully."

echo "[entrypoint] Checking whether a default admin user needs to be provisioned..."
if ! python -m app.scripts.bootstrap_default_admin; then
    echo "[entrypoint] WARNING: default admin bootstrap step failed. This is non-fatal -- " \
         "the server will still start. Create an admin user manually with: " \
         "docker compose exec backend_api python -m app.scripts.create_admin_user --tenant-name ... --email ..."
fi

echo "[entrypoint] Starting uvicorn..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "${GENBI_UVICORN_WORKERS:-2}"
