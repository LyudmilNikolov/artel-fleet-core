"""Who may see driver documents, document numbers and scans (personal data).

- admin, fleet_manager: everything.
- viewer: document entries (type, status, dates) with masked numbers; no scans.
- driver: only their own record (``drivers.user_id == user.id``): entries, full numbers and scans.
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import ComplianceDocument
from app.models.document import EntityType
from app.models.driver import Driver
from app.models.driver_document import DriverDocument
from app.models.expense import Expense
from app.models.maintenance import MaintenanceRecord
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle
from app.services.driver_scope import resolve_scope

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


def can_edit_driver_documents(user: User, driver: Driver) -> bool:
    """Managers manage every driver's documents; a driver adds, edits and renews their own."""
    return is_manager(user) or is_own_record(user, driver)


def can_view_scans_of(user: User, driver: Driver) -> bool:
    """Scanned files of a driver: managers and the driver themself only."""
    return is_manager(user) or is_own_record(user, driver)


async def vehicle_of_entity(session: AsyncSession, entity_type: EntityType, entity_id: UUID) -> UUID | None:
    """The vehicle a vehicle-linked document entity belongs to (None for other entity types or missing rows)."""
    if entity_type == EntityType.VEHICLE:
        return await session.scalar(select(Vehicle.id).where(Vehicle.id == entity_id))
    model = {
        EntityType.MAINTENANCE: MaintenanceRecord,
        EntityType.EXPENSE: Expense,
        EntityType.COMPLIANCE_DOCUMENT: ComplianceDocument,
    }.get(entity_type)
    if model is None:
        return None
    return await session.scalar(select(model.vehicle_id).where(model.id == entity_id))


async def can_upload_scans(session: AsyncSession, user: User, entity_type: EntityType, entity_id: UUID) -> bool:
    """Managers upload anywhere; a driver only scans of their own record or of their own document entries."""
    if is_manager(user):
        return True
    return (
        user.role == UserRole.DRIVER
        and entity_type in DRIVER_ENTITY_TYPES
        and await can_view_scans(session, user, entity_type, entity_id)
    )


async def can_view_scans(session: AsyncSession, user: User, entity_type: EntityType, entity_id: UUID) -> bool:
    """Access check for the documents feature.

    Driver records follow the driver rules above. For a driver-role user, files of vehicle-linked entities
    (vehicle, maintenance, expense, vehicle document) are limited to their assigned vehicles; other roles are
    not restricted here.
    """
    if entity_type not in DRIVER_ENTITY_TYPES:
        if user.role != UserRole.DRIVER:
            return True
        vehicle_id = await vehicle_of_entity(session, entity_type, entity_id)
        return (await resolve_scope(session, user)).allows_vehicle(vehicle_id)
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
