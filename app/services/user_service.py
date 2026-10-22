"""Creating and changing logins, including the link between a driver-role login and its driver profile.

A login with the ``driver`` role must be linked to an existing driver profile (created first under Drivers):
that profile decides which vehicle the driver may see. Other roles are never linked.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import Driver, DriverStatus
from app.models.user import User, UserRole
from app.repositories.driver_repo import DriverRepository
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.errors import DomainError


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
        if role == UserRole.DRIVER:
            if driver_id is None:
                raise DomainError("error.driver_profile_required")
            driver = await self._driver_to_link(driver_id, None)
        elif driver_id is not None:
            raise DomainError("error.driver_profile_role")
        user = await AuthService(self.session).register(
            email=email, username=username, full_name=full_name, password=password, role=role, language=language
        )
        if driver is not None:
            await self.drivers.update(driver, user_id=user.id)
        return user

    async def update(self, user: User, changes: dict, *, driver_id: UUID | None = None) -> User:
        """Apply field changes; keeps the driver-profile link consistent with the (new) role.

        When the role (or ``driver_id``) is part of the change:
        - role driver: a profile is required (the current one is kept unless ``driver_id`` names another)
        - any other role: the link is removed
        """
        if "role" not in changes and driver_id is None:
            return await self.users.update(user, **changes)  # e.g. deactivate: the link is untouched
        role = changes.get("role", user.role)
        current = await self.drivers.get_for_user(user.id)
        if role == UserRole.DRIVER:
            target_id = driver_id or (current.id if current else None)
            if target_id is None:
                raise DomainError("error.driver_profile_required")
            if current is None or target_id != current.id:
                target = await self._driver_to_link(target_id, user.id)
                if current is not None:
                    await self.drivers.update(current, user_id=None)
                await self.drivers.update(target, user_id=user.id)
        else:
            if driver_id is not None:
                raise DomainError("error.driver_profile_role")
            if current is not None:
                await self.drivers.update(current, user_id=None)
        return await self.users.update(user, **changes)
