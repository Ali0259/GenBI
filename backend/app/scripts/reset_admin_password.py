"""
app.scripts.reset_admin_password
-----------------------------------
Emergency-access CLI: resets an existing admin user's password directly in
the database, bypassing the normal "know your current password" requirement
of the /api/auth/change-password endpoint. Use this when you're locked out
(forgotten password, or you're stuck on the auto-generated default admin
password and want to set it to something specific rather than using the
Admin Panel's change-password form).

Usage:
    docker compose exec backend_api python -m app.scripts.reset_admin_password \\
        --email admin@genbi.local
"""

from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy import select

from app.database import AdminSessionLocal
from app.models.admin_models import AdminUser
from app.security import hash_password


def _parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Reset an existing admin user's password.")
    parser.add_argument("--email", required=True, help="Email of the existing admin user to reset.")
    parser.add_argument(
        "--password",
        required=False,
        default=None,
        help="New password. If omitted, you will be prompted securely (recommended).",
    )
    return parser.parse_args()


def main() -> int:
    arguments = _parse_arguments()

    password = arguments.password
    if password is None:
        password = getpass.getpass("Enter new password: ")
        password_confirmation = getpass.getpass("Confirm new password: ")
        if password != password_confirmation:
            print("Error: passwords do not match.", file=sys.stderr)
            return 1

    if len(password) < 8:
        print("Error: password must be at least 8 characters.", file=sys.stderr)
        return 1

    session = AdminSessionLocal()
    try:
        admin_user = session.scalar(select(AdminUser).where(AdminUser.email == arguments.email))
        if admin_user is None:
            print(f"Error: no admin user found with email '{arguments.email}'.", file=sys.stderr)
            return 1

        admin_user.hashed_password = hash_password(password)
        session.add(admin_user)
        session.commit()
        print(f"Password reset successfully for '{arguments.email}'.")
        return 0
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
