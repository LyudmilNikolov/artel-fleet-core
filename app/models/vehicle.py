import enum
import uuid
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, Enum, ForeignKey, Index, Integer, Numeric, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base, TimestampMixin, UUIDPrimaryKey
from app.vehicle_rules import FuelType, PowerSource, TransmissionType, VehicleType


class VehicleStatus(enum.StrEnum):
    ACTIVE = "active"
    IN_MAINTENANCE = "in_maintenance"
    DECOMMISSIONED = "decommissioned"
    RESERVED = "reserved"


class Vehicle(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "vehicles"

    license_plate: Mapped[str] = mapped_column(String(20), unique=True, nullable=False, index=True)
    vin: Mapped[str | None] = mapped_column(String(17), unique=True, nullable=True, index=True)
    brand: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    color: Mapped[str | None] = mapped_column(String(50), nullable=True)
    vehicle_type: Mapped[VehicleType] = mapped_column(
        Enum(VehicleType, name="vehicle_type", values_callable=lambda e: [v.value for v in e]), nullable=False
    )
    fuel_type: Mapped[FuelType | None] = mapped_column(
        Enum(FuelType, name="fuel_type", values_callable=lambda e: [v.value for v in e])
    )
    transmission: Mapped[TransmissionType | None] = mapped_column(
        Enum(TransmissionType, name="transmission_type", values_callable=lambda e: [v.value for v in e])
    )
    power_source: Mapped[PowerSource | None] = mapped_column(
        Enum(PowerSource, name="power_source", values_callable=lambda e: [v.value for v in e])
    )
    emission_standard: Mapped[str | None] = mapped_column(String(6))
    supported_container_sizes: Mapped[list[str] | None] = mapped_column(ARRAY(String(6)))
    seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    purchase_date: Mapped[str | None] = mapped_column(Date, nullable=True)
    purchase_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    current_mileage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[VehicleStatus] = mapped_column(
        Enum(VehicleStatus, name="vehicle_status"),
        default=VehicleStatus.ACTIVE,
        nullable=False,
    )
    assigned_driver_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("drivers.id", ondelete="SET NULL"), nullable=True
    )
    department: Mapped[str] = mapped_column(String(200), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    axle_count: Mapped[int | None] = mapped_column(Integer)
    engine_displacement_cc: Mapped[int | None] = mapped_column(Integer)
    co2_emission_class: Mapped[int | None] = mapped_column(Integer)
    engine_power_hp: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    max_permissible_mass_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    empty_weight_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    payload_capacity_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    lifting_capacity_kg: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    fuel_tank_capacity_l: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    adblue_capacity_l: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    cargo_length_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    cargo_width_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    cargo_height_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    max_lift_height_m: Mapped[Decimal | None] = mapped_column(Numeric(7, 3))
    operating_hours: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))

    __table_args__ = (
        # one driver, one vehicle: a driver cannot be assigned to two vehicles (a vehicle has one driver by design)
        Index(
            "uq_vehicles_assigned_driver",
            "assigned_driver_id",
            unique=True,
            postgresql_where=text("assigned_driver_id IS NOT NULL"),
        ),
        CheckConstraint("year IS NULL OR year BETWEEN 1900 AND 2100", name="vehicle_year_bounds"),
        CheckConstraint("axle_count IS NULL OR axle_count BETWEEN 1 AND 20", name="vehicle_axle_count_bounds"),
        CheckConstraint("seats IS NULL OR seats BETWEEN 1 AND 100", name="vehicle_seats_bounds"),
        CheckConstraint(
            "engine_displacement_cc IS NULL OR engine_displacement_cc BETWEEN 1 AND 100000",
            name="vehicle_engine_displacement_cc_bounds",
        ),
        CheckConstraint(
            "current_mileage IS NULL OR current_mileage BETWEEN 0 AND 10000000", name="vehicle_current_mileage_bounds"
        ),
        CheckConstraint(
            "co2_emission_class IS NULL OR co2_emission_class BETWEEN 1 AND 5", name="vehicle_co2_emission_class_bounds"
        ),
        CheckConstraint(
            "engine_power_hp IS NULL OR (engine_power_hp > 0 AND engine_power_hp <= 10000)",
            name="vehicle_engine_power_hp_bounds",
        ),
        CheckConstraint(
            "max_permissible_mass_kg IS NULL OR (max_permissible_mass_kg > 0 AND max_permissible_mass_kg <= 1000000)",
            name="vehicle_max_permissible_mass_kg_bounds",
        ),
        CheckConstraint(
            "empty_weight_kg IS NULL OR (empty_weight_kg > 0 AND empty_weight_kg <= 1000000)",
            name="vehicle_empty_weight_kg_bounds",
        ),
        CheckConstraint(
            "payload_capacity_kg IS NULL OR (payload_capacity_kg > 0 AND payload_capacity_kg <= 1000000)",
            name="vehicle_payload_capacity_kg_bounds",
        ),
        CheckConstraint(
            "lifting_capacity_kg IS NULL OR (lifting_capacity_kg > 0 AND lifting_capacity_kg <= 1000000)",
            name="vehicle_lifting_capacity_kg_bounds",
        ),
        CheckConstraint(
            "fuel_tank_capacity_l IS NULL OR (fuel_tank_capacity_l > 0 AND fuel_tank_capacity_l <= 10000)",
            name="vehicle_fuel_tank_capacity_l_bounds",
        ),
        CheckConstraint(
            "adblue_capacity_l IS NULL OR (adblue_capacity_l > 0 AND adblue_capacity_l <= 10000)",
            name="vehicle_adblue_capacity_l_bounds",
        ),
        CheckConstraint(
            "cargo_length_m IS NULL OR (cargo_length_m > 0 AND cargo_length_m <= 100)",
            name="vehicle_cargo_length_m_bounds",
        ),
        CheckConstraint(
            "cargo_width_m IS NULL OR (cargo_width_m > 0 AND cargo_width_m <= 100)", name="vehicle_cargo_width_m_bounds"
        ),
        CheckConstraint(
            "cargo_height_m IS NULL OR (cargo_height_m > 0 AND cargo_height_m <= 100)",
            name="vehicle_cargo_height_m_bounds",
        ),
        CheckConstraint(
            "max_lift_height_m IS NULL OR (max_lift_height_m > 0 AND max_lift_height_m <= 100)",
            name="vehicle_max_lift_height_m_bounds",
        ),
        CheckConstraint(
            "operating_hours IS NULL OR (operating_hours >= 0 AND operating_hours <= 1000000)",
            name="vehicle_operating_hours_bounds",
        ),
        CheckConstraint(
            "purchase_price IS NULL OR (purchase_price >= 0 AND purchase_price <= 9999999999.99)",
            name="vehicle_purchase_price_bounds",
        ),
        CheckConstraint(
            "adblue_capacity_l IS NULL OR vehicle_type IN ('tractor_unit')", name="vehicle_adblue_capacity_l_applicable"
        ),
        CheckConstraint(
            "axle_count IS NULL OR vehicle_type IN ('tractor_unit', 'container_semi_trailer', 'curtain_side_semi_trailer')",
            name="vehicle_axle_count_applicable",
        ),
        CheckConstraint(
            "cargo_height_m IS NULL OR vehicle_type IN ('curtain_side_semi_trailer')",
            name="vehicle_cargo_height_m_applicable",
        ),
        CheckConstraint(
            "cargo_length_m IS NULL OR vehicle_type IN ('curtain_side_semi_trailer')",
            name="vehicle_cargo_length_m_applicable",
        ),
        CheckConstraint(
            "cargo_width_m IS NULL OR vehicle_type IN ('curtain_side_semi_trailer')",
            name="vehicle_cargo_width_m_applicable",
        ),
        CheckConstraint(
            "co2_emission_class IS NULL OR vehicle_type IN ('tractor_unit')",
            name="vehicle_co2_emission_class_applicable",
        ),
        CheckConstraint(
            "current_mileage IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
            name="vehicle_current_mileage_applicable",
        ),
        CheckConstraint(
            "emission_standard IS NULL OR vehicle_type IN ('tractor_unit')", name="vehicle_emission_standard_applicable"
        ),
        CheckConstraint(
            "empty_weight_kg IS NULL OR vehicle_type IN ('container_semi_trailer', 'curtain_side_semi_trailer')",
            name="vehicle_empty_weight_kg_applicable",
        ),
        CheckConstraint(
            "engine_displacement_cc IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
            name="vehicle_engine_displacement_cc_applicable",
        ),
        CheckConstraint(
            "engine_power_hp IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
            name="vehicle_engine_power_hp_applicable",
        ),
        CheckConstraint(
            "fuel_tank_capacity_l IS NULL OR vehicle_type IN ('tractor_unit')",
            name="vehicle_fuel_tank_capacity_l_applicable",
        ),
        CheckConstraint(
            "fuel_type IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
            name="vehicle_fuel_type_applicable",
        ),
        CheckConstraint(
            "lifting_capacity_kg IS NULL OR vehicle_type IN ('forklift')", name="vehicle_lifting_capacity_kg_applicable"
        ),
        CheckConstraint(
            "max_lift_height_m IS NULL OR vehicle_type IN ('forklift')", name="vehicle_max_lift_height_m_applicable"
        ),
        CheckConstraint(
            "max_permissible_mass_kg IS NULL OR vehicle_type IN ('tractor_unit', 'container_semi_trailer', 'curtain_side_semi_trailer', 'van')",
            name="vehicle_max_permissible_mass_kg_applicable",
        ),
        CheckConstraint(
            "operating_hours IS NULL OR vehicle_type IN ('forklift')", name="vehicle_operating_hours_applicable"
        ),
        CheckConstraint(
            "payload_capacity_kg IS NULL OR vehicle_type IN ('container_semi_trailer', 'curtain_side_semi_trailer', 'van')",
            name="vehicle_payload_capacity_kg_applicable",
        ),
        CheckConstraint("power_source IS NULL OR vehicle_type IN ('forklift')", name="vehicle_power_source_applicable"),
        CheckConstraint("seats IS NULL OR vehicle_type IN ('van', 'passenger_car')", name="vehicle_seats_applicable"),
        CheckConstraint(
            "supported_container_sizes IS NULL OR vehicle_type IN ('container_semi_trailer')",
            name="vehicle_supported_container_sizes_applicable",
        ),
        CheckConstraint(
            "transmission IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
            name="vehicle_transmission_applicable",
        ),
        CheckConstraint(
            "length(trim(brand)) > 0 AND length(trim(model)) > 0 AND length(trim(department)) > 0",
            name="vehicle_required_text",
        ),
        CheckConstraint(
            "supported_container_sizes IS NULL OR supported_container_sizes <@ ARRAY['20ft','40ft','45ft','2x20ft']::varchar[]",
            name="vehicle_container_sizes",
        ),
        CheckConstraint(
            "emission_standard IS NULL OR emission_standard IN ('Euro 3','Euro 4','Euro 5','EEV','Euro 6')",
            name="vehicle_emission_standard",
        ),
        CheckConstraint(
            "fuel_type IS NULL OR (vehicle_type = 'tractor_unit' AND fuel_type IN ('diesel','lng','cng','electric')) OR (vehicle_type IN ('van','passenger_car') AND fuel_type IN ('gasoline','diesel','gas','electric','hybrid'))",
            name="vehicle_fuel_choice",
        ),
        CheckConstraint(
            "(vehicle_type = 'forklift' AND license_plate ~ '^PRIVATE-[0-9A-F]{8}$') OR (vehicle_type <> 'forklift' AND length(trim(license_plate)) > 0 AND license_plate NOT LIKE 'PRIVATE-%')",
            name="vehicle_identifier",
        ),
    )

    # Relationships
    assigned_driver = relationship("Driver", back_populates="assigned_vehicles", foreign_keys=[assigned_driver_id])

    def __repr__(self) -> str:
        return f"<Vehicle {self.license_plate} {self.brand} {self.model}>"
