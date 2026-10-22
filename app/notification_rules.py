"""Single registry of notification sources: every tracked document type that can need attention.

A source code is ``<owner>:<document type>``, e.g. ``vehicle:casco`` or ``driver:id_card``. Sources are derived from
``ComplianceType`` and ``DriverDocumentType``, so a new document type appears here without a migration; it only needs
a ``notification_source.<code>`` label in both catalogs (a test fails when one is missing).
"""

from dataclasses import dataclass
from datetime import time

from app.driver_document_rules import DriverDocumentType
from app.vehicle_rules import ComplianceType

VEHICLE = "vehicle"
DRIVER = "driver"

# Entity types an attention item points at (the document row itself).
VEHICLE_ENTITY = "compliance_document"
DRIVER_ENTITY = "driver_document"


@dataclass(frozen=True)
class NotificationSource:
    code: str
    owner: str  # VEHICLE | DRIVER
    document_type: str
    entity_type: str

    @property
    def label_key(self) -> str:
        return f"notification_source.{self.code}"


SOURCES: tuple[NotificationSource, ...] = (
    *(NotificationSource(f"{VEHICLE}:{t.value}", VEHICLE, t.value, VEHICLE_ENTITY) for t in ComplianceType),
    *(NotificationSource(f"{DRIVER}:{t.value}", DRIVER, t.value, DRIVER_ENTITY) for t in DriverDocumentType),
)
SOURCE_BY_CODE = {source.code: source for source in SOURCES}


def source_code(entity_type: str, document_type: str) -> str:
    """Source code of a document row."""
    owner = VEHICLE if entity_type == VEHICLE_ENTITY else DRIVER
    return f"{owner}:{document_type}"


def source_label_key(code: str) -> str:
    return f"notification_source.{code}"


# --- Code defaults: a source without a stored rule behaves like this (channels stay off until configured) ---

DEFAULT_MILESTONE_DAYS = (30, 15, 10)
DEFAULT_REPEAT_EVERY_DAYS = 1
DEFAULT_SEND_TIME = time(8, 0)
DEFAULT_TIMEZONE = "Europe/Sofia"

MAX_MILESTONES = 10
MAX_DAYS = 365

# Recipient kinds. Further kinds (the driver concerned, the vehicle's driver) can be added without a migration.
RECIPIENT_USER = "user"
RECIPIENT_ROLE = "role"
RECIPIENT_EMAIL = "external_email"
RECIPIENT_TELEGRAM = "external_telegram"
RECIPIENT_KINDS = (RECIPIENT_USER, RECIPIENT_ROLE, RECIPIENT_EMAIL, RECIPIENT_TELEGRAM)
# Roles that can be addressed as a group (drivers are out of scope for now).
RECIPIENT_ROLES = ("admin", "fleet_manager", "viewer")
