"""What a driver-role login may see: their own driver profile and, only when vehicles are assigned, those vehicles.

- A driver login is linked to one driver profile (``drivers.user_id``).
- Without assigned vehicles the driver sees nothing but that profile (plus account pages: profile, help, notifications).
- With assigned vehicles the driver also sees, read-only, those vehicles and their expenses, maintenance,
  documents & deadlines and files. Other roles are not restricted here.

The page allow-list below is the single place that decides which web pages a driver may open; list and detail
handlers additionally filter by ``DriverScope.vehicle_ids``.
"""

import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.driver import Driver, DriverStatus
from app.models.user import User, UserRole
from app.models.vehicle import Vehicle


@dataclass(frozen=True)
class DriverScope:
    """Visibility of one request's user; ``restricted`` is True only for driver-role accounts."""

    restricted: bool = False
    driver_id: UUID | None = None
    vehicle_ids: tuple[UUID, ...] = ()

    @property
    def has_vehicles(self) -> bool:
        return bool(self.vehicle_ids)

    def allows_vehicle(self, vehicle_id: UUID | None) -> bool:
        """Unrestricted users see every vehicle; a driver only their assigned ones."""
        return not self.restricted or (vehicle_id is not None and vehicle_id in self.vehicle_ids)

    @property
    def vehicle_filter(self) -> tuple[UUID, ...] | None:
        """Vehicle ids to filter queries by, or None for no restriction."""
        return self.vehicle_ids if self.restricted else None

    @property
    def home(self) -> str:
        """Where a driver lands: their own profile, or the account page when no profile is linked."""
        return f"/drivers/{self.driver_id}" if self.driver_id else "/profile"


UNRESTRICTED = DriverScope()


async def resolve_scope(session: AsyncSession, user: User) -> DriverScope:
    """The scope of a user; a terminated driver keeps their profile but loses vehicle access."""
    if user.role != UserRole.DRIVER:
        return UNRESTRICTED
    driver = await session.scalar(select(Driver).where(Driver.user_id == user.id))
    if driver is None:
        return DriverScope(restricted=True)
    vehicle_ids: tuple[UUID, ...] = ()
    if driver.status != DriverStatus.TERMINATED:
        rows = await session.scalars(select(Vehicle.id).where(Vehicle.assigned_driver_id == driver.id))
        vehicle_ids = tuple(rows.all())
    return DriverScope(restricted=True, driver_id=driver.id, vehicle_ids=vehicle_ids)


# --- web page allow-list -------------------------------------------------------------------------

_UUID = r"[0-9a-fA-F-]{36}"
# Account pages every login needs, any method.
_ACCOUNT_PREFIXES = ("/login", "/logout", "/profile", "/set-language", "/static", "/help", "/notifications")
# Read-only pages for assigned vehicles (GET only).
_VEHICLE_PAGES = tuple(
    re.compile(p)
    for p in (
        r"^/vehicles$",
        rf"^/vehicles/{_UUID}$",
        rf"^/vehicles/{_UUID}/tab/(mileage|maintenance|expenses|compliance)$",
        r"^/expenses$",
        rf"^/expenses/{_UUID}$",
        r"^/maintenance$",
        r"^/maintenance/due$",
        rf"^/maintenance/{_UUID}$",
        r"^/compliance$",
        rf"^/compliance/{_UUID}$",
        r"^/documents/gallery/[a-z_]+/" + _UUID + "$",
        rf"^/documents/{_UUID}/download$",
    )
)
_DRIVER_DOCUMENT_PAGE = re.compile(rf"^/driver-documents/{_UUID}$")
# A driver manages their own documents: add / edit / renew entries, upload and remove their own scans.
# The handlers check that the record belongs to this driver.
_OWN_DOCUMENT_RULES = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        ("GET", r"^/driver-documents/new$"),
        ("POST", r"^/driver-documents/new$"),
        ("GET", rf"^/driver-documents/{_UUID}/(edit|renew)$"),
        ("POST", rf"^/driver-documents/{_UUID}/(edit|renew)$"),
        ("POST", r"^/documents/upload$"),
        ("DELETE", rf"^/documents/{_UUID}$"),
        ("GET", rf"^/documents/gallery/(driver|driver_document)/{_UUID}$"),
        ("GET", rf"^/documents/{_UUID}/download$"),
    )
)


def driver_may_open(path: str, method: str, scope: DriverScope) -> bool:
    """Whether a driver-role user may request this web path. Everything not listed is closed."""
    path = path.rstrip("/") or "/"
    if any(path == p or path.startswith(p + "/") for p in _ACCOUNT_PREFIXES):
        return True
    if scope.driver_id and any(m == method and p.match(path) for m, p in _OWN_DOCUMENT_RULES):
        return True
    if method not in ("GET", "HEAD"):
        return False
    if scope.driver_id:
        own = f"/drivers/{scope.driver_id}"
        if path == own or re.fullmatch(re.escape(own) + r"/tab/[a-z_]+", path):
            return True
        if _DRIVER_DOCUMENT_PAGE.match(path):  # the handler checks the entry belongs to this driver
            return True
    return scope.has_vehicles and any(p.match(path) for p in _VEHICLE_PAGES)


# --- API allow-list (deny by default; the API is for integrations, not the driver UI) ----------------

_API_ALWAYS = (r"^/api/v1/auth(/.*)?$",)
_API_DRIVER_READ = (
    r"^/api/v1/vehicles$",
    rf"^/api/v1/vehicles/{_UUID}$",
    rf"^/api/v1/mileage/vehicle/{_UUID}$",
    r"^/api/v1/driver-documents.*$",
)


def driver_may_call_api(path: str, method: str, scope: DriverScope) -> bool:
    """Whether a driver-role token may call this API path (record-level checks stay in the handlers)."""
    path = path.rstrip("/") or "/"
    if any(re.match(p, path) for p in _API_ALWAYS):
        return True
    if method in ("POST",) and re.match(r"^/api/v1/mileage(/bulk)?$", path):
        return scope.has_vehicles  # submit readings for assigned vehicles
    if method not in ("GET", "HEAD"):
        return False
    if scope.driver_id and path == f"/api/v1/drivers/{scope.driver_id}":
        return True
    if re.match(r"^/api/v1/driver-documents.*$", path):
        return True
    if re.match(rf"^/api/v1/documents/(entity/[a-z_]+/{_UUID}|{_UUID}/download)$", path):
        return scope.driver_id is not None
    return scope.has_vehicles and any(re.match(p, path) for p in _API_DRIVER_READ)
