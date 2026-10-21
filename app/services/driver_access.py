"""Who may see driver documents, document numbers and scans (personal data).

- admin, fleet_manager: everything.
- viewer: document entries (type, status, dates) with masked numbers; no scans.
- driver: only their own record (``drivers.user_id == user.id``): entries, full numbers and scans.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.document import EntityType
from app.models.driver import Driver
from app.models.driver_document import DriverDocument
from app.models.user import User, UserRole

MANAGER_ROLES = (UserRole.ADMIN, UserRole.FLEET_MANAGER)
DRIVER_ENTITY_TYPES = (EntityType.DRIVER, EntityType.DRIVER_DOCUMENT)


def is_manager(user: User) -> bool:
    """True for roles that may create, edit, renew and delete driver data."""
    return user.role in MANAGER_ROLES


def is_own_record(user: User, driver: Driver) -> bool:
    """True when the user is a driver-role account linked to this driver record."""
    return user.role == UserRole.DRIVER and driver.user_id is not None and driver.user_id == user.id


def can_read_driver_documents(user: User, driver: Driver) -> bool:
    """Managers and viewers read every driver's entries; a driver only their own."""
    return is_manager(user) or user.role == UserRole.VIEWER or is_own_record(user, driver)


def can_see_numbers(user: User, driver: Driver) -> bool:
    """Full document numbers for managers and the driver themself; everyone else sees them masked."""
    return is_manager(user) or is_own_record(user, driver)


def can_view_scans_of(user: User, driver: Driver) -> bool:
    """Scanned files of a driver: managers and the driver themself only."""
    return is_manager(user) or is_own_record(user, driver)


async def can_view_scans(session: AsyncSession, user: User, entity_type: EntityType, entity_id: UUID) -> bool:
    """Access check for the documents feature; non-driver entities are not restricted here."""
    if entity_type not in DRIVER_ENTITY_TYPES:
        return True
    if is_manager(user):
        return True
    if entity_type == EntityType.DRIVER_DOCUMENT:
        driver_id = await session.scalar(select(DriverDocument.driver_id).where(DriverDocument.id == entity_id))
    else:
        driver_id = entity_id
    if driver_id is None:
        return False
    driver = await session.get(Driver, driver_id)
    return driver is not None and is_own_record(user, driver)
