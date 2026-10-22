"""Creating and changing logins, including the link between a driver-role login and its driver profile.

A login with the ``driver`` role must be linked to an existing driver profile (created first under Drivers):
that profile decides which vehicle the driver may see. Other roles are never linked.
"""

import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import Driver, DriverStatus
from app.models.user import User, UserRole
from app.repositories.driver_repo import DriverRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.errors import DomainError

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_email(value: str | None) -> bool:
    return bool(value) and len(value) <= 255 and bool(EMAIL_PATTERN.match(value))


class UserService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.users = UserRepository(session)
        self.drivers = DriverRepository(session)

    async def linkable_drivers(self, user_id: UUID | None = None) -> list[Driver]:
        """Driver profiles a driver login can be linked to: not terminated and without a login (plus the user's own)."""
        query = select(Driver).where(Driver.status != DriverStatus.TERMINATED)
        free = Driver.user_id.is_(None)
        query = query.where(free | (Driver.user_id == user_id) if user_id else free)
        return list((await self.session.scalars(query.order_by(Driver.full_name))).all())

    async def _driver_to_link(self, driver_id: UUID, user_id: UUID | None) -> Driver:
        """The chosen profile, checked: it exists, is active and is not linked to another login."""
        driver = await self.drivers.get_by_id(driver_id)
        if driver is None:
            raise DomainError("error.driver_profile_not_found")
        if driver.status == DriverStatus.TERMINATED:
            raise DomainError("error.driver_profile_terminated")
        if driver.user_id is not None and driver.user_id != user_id:
            raise DomainError("error.driver_profile_taken")
        return driver

    async def create(
        self,
        *,
        email: str,
        username: str,
        full_name: str,
        password: str,
        role: UserRole,
        language: str,
        driver_id: UUID | None = None,
    ) -> User:
        """Create a login; a driver login needs ``driver_id`` of an existing, unlinked driver profile."""
        driver = None
        save_email_to_profile = False
        if role == UserRole.DRIVER:
            if driver_id is None:
                raise DomainError("error.driver_profile_required")
            driver = await self._driver_to_link(driver_id, None)
            # the driver profile is the source of the login's name and email
            full_name = driver.full_name
            if driver.email:
                email = driver.email
            else:
                if not valid_email(email):
                    raise DomainError("error.user_email_invalid")
                save_email_to_profile = True  # the profile has none: the entered address is saved there too
        elif driver_id is not None:
            raise DomainError("error.driver_profile_role")
        user = await AuthService(self.session).register(
            email=email, username=username, full_name=full_name, password=password, role=role, language=language
        )
        if driver is not None:
            await self.drivers.update(driver, user_id=user.id, **({"email": email} if save_email_to_profile else {}))
        return user

    async def update(self, user: User, changes: dict, *, driver_id: UUID | None = None) -> User:
        """Apply field changes; keeps the driver-profile link consistent with the (new) role.

        When the role (or ``driver_id``) is part of the change:
        - role driver: a profile is required (the current one is kept unless ``driver_id`` names another);
          the login's name and email then come from that profile (an email entered for a profile without one
          is saved to the profile too)
        - any other role: the link is removed
        Username and email must stay unique.
        """
        changes = dict(changes)
        if "role" not in changes and driver_id is None:
            await self._check_unique(user, changes)
            return await self.users.update(user, **changes)  # e.g. deactivate: the link is untouched
        role = changes.get("role", user.role)
        current = await self.drivers.get_for_user(user.id)
        profile = None  # the profile the login will be linked to
        if role == UserRole.DRIVER:
            target_id = driver_id or (current.id if current else None)
            if target_id is None:
                raise DomainError("error.driver_profile_required")
            profile = current if current and target_id == current.id else await self._driver_to_link(target_id, user.id)
            changes["full_name"] = profile.full_name
            if profile.email:
                changes["email"] = profile.email
            else:
                email = changes.get("email") or user.email
                if not valid_email(email):
                    raise DomainError("error.user_email_invalid")
                changes["email"] = email
        elif driver_id is not None:
            raise DomainError("error.driver_profile_role")
        await self._check_unique(user, changes)
        # every check passed: now write
        if current is not None and (profile is None or profile.id != current.id):
            await self.drivers.update(current, user_id=None)
        if profile is not None:
            fields = {"user_id": user.id}
            if not profile.email:
                fields["email"] = changes["email"]  # the profile had none: the entered address is saved there too
            await self.drivers.update(profile, **fields)
        return await self.users.update(user, **changes)

    async def _check_unique(self, user: User, changes: dict) -> None:
        """A changed username or email must not belong to another login."""
        username = changes.get("username")
        if username is not None and username != user.username:
            if not 3 <= len(username) <= 100:
                raise DomainError("error.user_username_invalid")
            other = await self.users.get_by_username(username)
            if other and other.id != user.id:
                raise DomainError("auth.username_taken")
        email = changes.get("email")
        if email is not None and email != user.email:
            other = await self.users.get_by_email(email)
            if other and other.id != user.id:
                raise DomainError("auth.email_registered")

    async def profile_for_form(self, role: str, driver_id: str, user: User | None = None) -> Driver | None:
        """The driver profile a form refers to (chosen one, else the user's current one); None for other roles."""
        if role != UserRole.DRIVER.value:
            return None
        try:
            chosen = UUID(driver_id) if driver_id else None
        except ValueError:
            return None
        if chosen is None and user is not None:
            current = await self.drivers.get_for_user(user.id)
            return current
        return await self.drivers.get_by_id(chosen) if chosen else None
