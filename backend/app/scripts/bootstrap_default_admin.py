"""
app.scripts.bootstrap_default_admin
--------------------------------------
Runs automatically on every backend container start (invoked from
entrypoint.sh, after migrations, before uvicorn starts). If -- and only if
-- zero AdminUser rows exist anywhere in the admin database, this creates a
default tenant and a default superadmin user with a freshly generated
random password, and writes the credentials to a file on a bind-mounted
volume so the operator can read them from the host filesystem immediately
after installation.

Idempotency is the whole point: on every subsequent container start
(restarts, upgrades, redeploys), this script sees that at least one
AdminUser already exists and exits immediately without doing anything --
it will NEVER overwrite an existing password or create a duplicate account.

This is deliberately separate from app.scripts.create_admin_user, which
remains available for an operator to explicitly provision additional users
(e.g. a second tenant) at any time.
"""

from __future__ import annotations

import datetime
import os
import secrets
import sys
from pathlib import Path

from sqlalchemy import select

from app.database import AdminSessionLocal
from app.models.admin_models import AdminUser, Tenant
from app.security import hash_password

_DEFAULT_TENANT_NAME = "Default Tenant"
_DEFAULT_ADMIN_EMAIL_ENV_VAR = "GENBI_DEFAULT_ADMIN_EMAIL"
_DEFAULT_ADMIN_EMAIL_FALLBACK = "admin@genbi.local"
_CREDENTIALS_FILE_PATH = Path("/opt/genbi/secrets/admin_credentials.txt")


def _generate_secure_password() -> str:
    # 18 bytes of entropy, url-safe encoding -- long enough to be a strong
    # password on its own, short enough to type by hand if the credentials
    # file is ever unavailable and someone has to read it off a screen.
    return secrets.token_urlsafe(18)


def _write_credentials_file(email: str, password: str) -> None:
    """
    Writes the generated credentials to a bind-mounted file the operator can
    read directly from the host (see docker-compose.yml's `./secrets:/opt/genbi/secrets`
    mount and install.sh, which prints this file's contents at the end of
    a fresh install). Silently skips writing if the mount point isn't
    present -- the credentials are still usable via container stdout in
    that case, they just won't persist to a host file.
    """
    try:
        _CREDENTIALS_FILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"[bootstrap_default_admin] WARNING: could not create secrets directory: {exc}", file=sys.stderr)
        return

    file_contents = (
        "GenBI Platform -- Default Admin Credentials\n"
        "=============================================\n"
        f"Generated at: {datetime.datetime.now(datetime.timezone.utc).isoformat()}\n\n"
        f"Email:    {email}\n"
        f"Password: {password}\n\n"
        "This file was generated automatically on first install because no\n"
        "admin user existed yet. Log in with these credentials, then\n"
        "IMMEDIATELY change this password (or create a personal admin\n"
        "account and deactivate this one) via the Admin Panel.\n\n"
        "This script will never overwrite this file or create another\n"
        "default admin as long as at least one admin user exists.\n"
    )

    try:
        _CREDENTIALS_FILE_PATH.write_text(file_contents)
        os.chmod(_CREDENTIALS_FILE_PATH, 0o600)
    except OSError as exc:
        print(f"[bootstrap_default_admin] WARNING: could not write credentials file: {exc}", file=sys.stderr)


def main() -> int:
    session = AdminSessionLocal()
    try:
        any_admin_exists = session.scalar(select(AdminUser).limit(1)) is not None
        if any_admin_exists:
            print("[bootstrap_default_admin] An admin user already exists. Skipping default admin creation.")
            return 0

        print("[bootstrap_default_admin] No admin users found. Creating a default tenant and admin user...")

        tenant = session.scalar(select(Tenant).where(Tenant.display_name == _DEFAULT_TENANT_NAME))
        if tenant is None:
            tenant = Tenant(display_name=_DEFAULT_TENANT_NAME)
            session.add(tenant)
            session.commit()
            session.refresh(tenant)

        default_email = os.environ.get(_DEFAULT_ADMIN_EMAIL_ENV_VAR, _DEFAULT_ADMIN_EMAIL_FALLBACK)
        generated_password = _generate_secure_password()

        default_admin_user = AdminUser(
            tenant_id=tenant.id,
            email=default_email,
            hashed_password=hash_password(generated_password),
            is_superadmin=True,
        )
        session.add(default_admin_user)
        session.commit()

        _write_credentials_file(default_email, generated_password)

        # Printed to container stdout (visible via `docker compose logs backend_api`)
        # as a fallback in case the bind-mounted credentials file is unavailable.
        print("=" * 70)
        print("DEFAULT ADMIN CREDENTIALS (also written to /opt/genbi/secrets/admin_credentials.txt)")
        print(f"  Email:    {default_email}")
        print(f"  Password: {generated_password}")
        print("  CHANGE THIS PASSWORD IMMEDIATELY AFTER FIRST LOGIN.")
        print("=" * 70)
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
