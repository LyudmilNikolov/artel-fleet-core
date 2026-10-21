"""Create a superuser (admin) account."""

import argparse
import asyncio
import sys

from app.database import AsyncSessionLocal
from app.i18n import _
from app.models.user import UserRole
from app.services.auth_service import AuthService
from app.services.errors import render_error


async def main(locale="bg"):
    username = input(_("cli.username", locale)).strip() or "admin"
    email = input(_("cli.email", locale)).strip() or "admin@fleetcore.local"
    full_name = input(_("cli.full_name", locale)).strip() or "Administrator"
    password = input(_("cli.password", locale)).strip()
    if not password:
        print(_("cli.password_required", locale), file=sys.stderr)
        sys.exit(1)
    if len(password) < 8:
        print(_("cli.password_length", locale), file=sys.stderr)
        sys.exit(1)

    async with AsyncSessionLocal() as session:
        service = AuthService(session)
        try:
            user = await service.register(
                email=email,
                username=username,
                full_name=full_name,
                password=password,
                role=UserRole.ADMIN,
            )
            await session.commit()
            print(_("cli.created", locale, username=user.username, email=user.email))
        except ValueError as e:
            print(render_error(e, locale), file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--locale", choices=("bg", "en"), default="bg")
    asyncio.run(main(parser.parse_args().locale))
