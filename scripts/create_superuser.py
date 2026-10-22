"""Create a superuser (admin) account.

Interactive:        python scripts/create_superuser.py
Non-interactive:    ADMIN_USERNAME=... ADMIN_EMAIL=... ADMIN_PASSWORD=... python scripts/create_superuser.py --from-env
                    (optional ADMIN_FULL_NAME; does nothing when the username already exists, so it is safe to re-run)
"""

import argparse
import asyncio
import os
import sys

from app.database import AsyncSessionLocal
from app.i18n import _
from app.models.user import UserRole
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.errors import render_error
from app.utils.password_policy import MIN_LENGTH


async def create_admin(session, *, username: str, email: str, full_name: str, password: str):
    """Create the admin; returns (user, created). An existing username is left untouched."""
    existing = await UserRepository(session).get_by_username(username)
    if existing:
        return existing, False
    user = await AuthService(session).register(
        email=email, username=username, full_name=full_name, password=password, role=UserRole.ADMIN
    )
    return user, True


async def main(locale="bg", from_env=False):
    if from_env:
        username = os.environ.get("ADMIN_USERNAME", "").strip()
        email = os.environ.get("ADMIN_EMAIL", "").strip()
        full_name = os.environ.get("ADMIN_FULL_NAME", "").strip() or "Administrator"
        password = os.environ.get("ADMIN_PASSWORD", "")
        if not (username and email and password):
            print("ADMIN_USERNAME, ADMIN_EMAIL and ADMIN_PASSWORD must be set", file=sys.stderr)
            sys.exit(1)
    else:
        username = input(_("cli.username", locale)).strip() or "admin"
        email = input(_("cli.email", locale)).strip() or "admin@fleetcore.local"
        full_name = input(_("cli.full_name", locale)).strip() or "Administrator"
        password = input(_("cli.password", locale)).strip()
    if not password:
        print(_("cli.password_required", locale), file=sys.stderr)
        sys.exit(1)
    if len(password) < MIN_LENGTH:
        print(_("cli.password_length", locale), file=sys.stderr)
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        try:
            user, created = await create_admin(
                session, username=username, email=email, full_name=full_name, password=password
            )
            await session.commit()
        except ValueError as e:
            print(render_error(e, locale), file=sys.stderr)
            sys.exit(1)
        if created:
            print(_("cli.created", locale, username=user.username, email=user.email))
        else:
            print(f"User {user.username} already exists: nothing changed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--locale", choices=("bg", "en"), default="bg")
    parser.add_argument(
        "--from-env", action="store_true", help="read ADMIN_USERNAME/EMAIL/PASSWORD from the environment"
    )
    args = parser.parse_args()
    asyncio.run(main(args.locale, args.from_env))
