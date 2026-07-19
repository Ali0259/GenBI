"""
app.scripts.create_admin_user
--------------------------------
One-time bootstrap CLI for creating the very first tenant and superadmin
user in a fresh installation. There is deliberately no seeded default
account in this platform (a hardcoded default credential is a well-known
attack surface), so this script -- or the Admin Panel's own user-invite
flow, once at least one superadmin exists to use it -- is the only way in.

Usage (run inside the backend_api container, which already has every
dependency and the correct ADMIN_DATABASE_URL / GENBI_MASTER_ENCRYPTION_KEY
environment variables set):

    docker compose exec backend_api python -m app.scripts.create_admin_user \\
        --tenant-name "Acme Corp" \\
        --email admin@acme.com \\
        --password "a-strong-password-here"

If a tenant with the given --tenant-name already exists, this script reuses
it rather than creating a duplicate. Safe to re-run: it will refuse to
create a second user with an email that already exists, rather than
silently overwriting a password.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.database import AdminSessionLocal
from app.models.admin_models import AdminUser, Tenant
from app.security import hash_password


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bootstrap the first tenant and superadmin user.")
    parser.add_argument("--tenant-name", required=True, help="Display name for the tenant, e.g. 'Acme Corp'.")
    parser.add_argument("--email", required=True, help="Login email for the new superadmin user.")
    parser.add_argument(
        "--password",
        required=False,
        default=None,
        help="Password for the new user. If omitted, you will be prompted securely (recommended, "
             "since --password is visible in shell history).",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()

    password = arguments.password
    if password is None:
        password = getpass.getpass("Enter password for the new admin user: ")
        password_confirmation = getpass.getpass("Confirm password: ")
        if password != password_confirmation:
            print("Error: passwords do not match.", file=sys.stderr)
            return 1

    if len(password) < 8:
        print("Error: password must be at least 8 characters.", file=sys.stderr)
        return 1

    session = AdminSessionLocal()
    try:
        existing_user = session.scalar(select(AdminUser).where(AdminUser.email == arguments.email))
        if existing_user is not None:
            print(f"Error: a user with email '{arguments.email}' already exists. "
                  f"Refusing to overwrite. Use the Admin Panel to reset a password instead.", file=sys.stderr)
            return 1

        tenant = session.scalar(select(Tenant).where(Tenant.display_name == arguments.tenant_name))
        if tenant is None:
            tenant = Tenant(display_name=arguments.tenant_name)
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
            print(f"Created new tenant '{tenant.display_name}' (id={tenant.id}).")
        else:
            print(f"Reusing existing tenant '{tenant.display_name}' (id={tenant.id}).")

        new_admin_user = AdminUser(
            tenant_id=tenant.id,
            email=arguments.email,
            hashed_password=hash_password(password),
            is_superadmin=True,
        )
        session.add(new_admin_user)
        session.commit()
        print(f"Created superadmin user '{new_admin_user.email}'. You can now log in at the Admin Panel.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
