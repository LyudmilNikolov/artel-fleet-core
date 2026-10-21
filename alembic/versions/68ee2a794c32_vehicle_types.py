"""Six authoritative vehicle types; requires separately cleaned empty vehicle table.

Revision ID: 68ee2a794c32
Revises: 57dd1f893b21
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "68ee2a794c32"
down_revision = "57dd1f893b21"
branch_labels = None
depends_on = None


def require_empty():
    op.execute("LOCK TABLE vehicles IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().execute(sa.text("SELECT EXISTS (SELECT 1 FROM vehicles)")).scalar():
        raise RuntimeError(
            "Vehicle migration requires an empty vehicles table. Run the separate reviewed demo cleanup first."
        )


def upgrade():
    require_empty()
    op.drop_column("vehicles", "body_type")
    op.drop_column("vehicles", "engine_volume")
    op.execute("DROP TYPE body_type")
    op.drop_column("vehicles", "fuel_type")
    op.drop_column("vehicles", "transmission")
    op.execute("DROP TYPE fuel_type")
    op.execute("DROP TYPE transmission_type")
    postgresql.ENUM(
        "tractor_unit",
        "container_semi_trailer",
        "curtain_side_semi_trailer",
        "van",
        "passenger_car",
        "forklift",
        name="vehicle_type",
    ).create(op.get_bind())
    postgresql.ENUM("gasoline", "diesel", "gas", "electric", "hybrid", "lng", "cng", name="fuel_type").create(
        op.get_bind()
    )
    postgresql.ENUM("manual", "automatic", name="transmission_type").create(op.get_bind())
    postgresql.ENUM("diesel", "lpg", "electric", name="power_source").create(op.get_bind())
    op.add_column(
        "vehicles", sa.Column("vehicle_type", postgresql.ENUM(name="vehicle_type", create_type=False), nullable=False)
    )
    op.add_column(
        "vehicles", sa.Column("fuel_type", postgresql.ENUM(name="fuel_type", create_type=False), nullable=True)
    )
    op.add_column(
        "vehicles",
        sa.Column("transmission", postgresql.ENUM(name="transmission_type", create_type=False), nullable=True),
    )
    op.add_column("vehicles", sa.Column("adblue_capacity_l", sa.Numeric(10, 2), nullable=True))
    op.add_column("vehicles", sa.Column("axle_count", sa.Integer(), nullable=True))
    op.add_column("vehicles", sa.Column("cargo_height_m", sa.Numeric(7, 3), nullable=True))
    op.add_column("vehicles", sa.Column("cargo_length_m", sa.Numeric(7, 3), nullable=True))
    op.add_column("vehicles", sa.Column("cargo_width_m", sa.Numeric(7, 3), nullable=True))
    op.add_column("vehicles", sa.Column("co2_emission_class", sa.Integer(), nullable=True))
    op.add_column("vehicles", sa.Column("emission_standard", sa.String(6), nullable=True))
    op.add_column("vehicles", sa.Column("empty_weight_kg", sa.Numeric(12, 2), nullable=True))
    op.add_column("vehicles", sa.Column("engine_displacement_cc", sa.Integer(), nullable=True))
    op.add_column("vehicles", sa.Column("engine_power_hp", sa.Numeric(8, 2), nullable=True))
    op.add_column("vehicles", sa.Column("fuel_tank_capacity_l", sa.Numeric(10, 2), nullable=True))
    op.add_column("vehicles", sa.Column("lifting_capacity_kg", sa.Numeric(12, 2), nullable=True))
    op.add_column("vehicles", sa.Column("max_lift_height_m", sa.Numeric(7, 3), nullable=True))
    op.add_column("vehicles", sa.Column("max_permissible_mass_kg", sa.Numeric(12, 2), nullable=True))
    op.add_column("vehicles", sa.Column("operating_hours", sa.Numeric(10, 1), nullable=True))
    op.add_column("vehicles", sa.Column("payload_capacity_kg", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "vehicles", sa.Column("power_source", postgresql.ENUM(name="power_source", create_type=False), nullable=True)
    )
    op.add_column("vehicles", sa.Column("supported_container_sizes", postgresql.ARRAY(sa.String(6)), nullable=True))
    op.alter_column("vehicles", "vin", nullable=True)
    op.alter_column("vehicles", "year", nullable=True)
    op.alter_column("vehicles", "current_mileage", nullable=True)
    op.alter_column("vehicles", "department", nullable=False)
    op.create_check_constraint(
        "vehicle_adblue_capacity_l_applicable",
        "vehicles",
        "adblue_capacity_l IS NULL OR vehicle_type IN ('tractor_unit')",
    )
    op.create_check_constraint(
        "vehicle_adblue_capacity_l_bounds",
        "vehicles",
        "adblue_capacity_l IS NULL OR (adblue_capacity_l > 0 AND adblue_capacity_l <= 10000)",
    )
    op.create_check_constraint(
        "vehicle_axle_count_applicable",
        "vehicles",
        "axle_count IS NULL OR vehicle_type IN ('tractor_unit', 'container_semi_trailer', 'curtain_side_semi_trailer')",
    )
    op.create_check_constraint(
        "vehicle_axle_count_bounds", "vehicles", "axle_count IS NULL OR axle_count BETWEEN 1 AND 20"
    )
    op.create_check_constraint(
        "vehicle_cargo_height_m_applicable",
        "vehicles",
        "cargo_height_m IS NULL OR vehicle_type IN ('curtain_side_semi_trailer')",
    )
    op.create_check_constraint(
        "vehicle_cargo_height_m_bounds",
        "vehicles",
        "cargo_height_m IS NULL OR (cargo_height_m > 0 AND cargo_height_m <= 100)",
    )
    op.create_check_constraint(
        "vehicle_cargo_length_m_applicable",
        "vehicles",
        "cargo_length_m IS NULL OR vehicle_type IN ('curtain_side_semi_trailer')",
    )
    op.create_check_constraint(
        "vehicle_cargo_length_m_bounds",
        "vehicles",
        "cargo_length_m IS NULL OR (cargo_length_m > 0 AND cargo_length_m <= 100)",
    )
    op.create_check_constraint(
        "vehicle_cargo_width_m_applicable",
        "vehicles",
        "cargo_width_m IS NULL OR vehicle_type IN ('curtain_side_semi_trailer')",
    )
    op.create_check_constraint(
        "vehicle_cargo_width_m_bounds",
        "vehicles",
        "cargo_width_m IS NULL OR (cargo_width_m > 0 AND cargo_width_m <= 100)",
    )
    op.create_check_constraint(
        "vehicle_co2_emission_class_applicable",
        "vehicles",
        "co2_emission_class IS NULL OR vehicle_type IN ('tractor_unit')",
    )
    op.create_check_constraint(
        "vehicle_co2_emission_class_bounds",
        "vehicles",
        "co2_emission_class IS NULL OR co2_emission_class BETWEEN 1 AND 5",
    )
    op.create_check_constraint(
        "vehicle_container_sizes",
        "vehicles",
        "supported_container_sizes IS NULL OR supported_container_sizes <@ ARRAY['20ft','40ft','45ft','2x20ft']::varchar[]",
    )
    op.create_check_constraint(
        "vehicle_current_mileage_applicable",
        "vehicles",
        "current_mileage IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
    )
    op.create_check_constraint(
        "vehicle_current_mileage_bounds",
        "vehicles",
        "current_mileage IS NULL OR current_mileage BETWEEN 0 AND 10000000",
    )
    op.create_check_constraint(
        "vehicle_emission_standard",
        "vehicles",
        "emission_standard IS NULL OR emission_standard IN ('Euro 3','Euro 4','Euro 5','EEV','Euro 6')",
    )
    op.create_check_constraint(
        "vehicle_emission_standard_applicable",
        "vehicles",
        "emission_standard IS NULL OR vehicle_type IN ('tractor_unit')",
    )
    op.create_check_constraint(
        "vehicle_empty_weight_kg_applicable",
        "vehicles",
        "empty_weight_kg IS NULL OR vehicle_type IN ('container_semi_trailer', 'curtain_side_semi_trailer')",
    )
    op.create_check_constraint(
        "vehicle_empty_weight_kg_bounds",
        "vehicles",
        "empty_weight_kg IS NULL OR (empty_weight_kg > 0 AND empty_weight_kg <= 1000000)",
    )
    op.create_check_constraint(
        "vehicle_engine_displacement_cc_applicable",
        "vehicles",
        "engine_displacement_cc IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
    )
    op.create_check_constraint(
        "vehicle_engine_displacement_cc_bounds",
        "vehicles",
        "engine_displacement_cc IS NULL OR engine_displacement_cc BETWEEN 1 AND 100000",
    )
    op.create_check_constraint(
        "vehicle_engine_power_hp_applicable",
        "vehicles",
        "engine_power_hp IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
    )
    op.create_check_constraint(
        "vehicle_engine_power_hp_bounds",
        "vehicles",
        "engine_power_hp IS NULL OR (engine_power_hp > 0 AND engine_power_hp <= 10000)",
    )
    op.create_check_constraint(
        "vehicle_fuel_choice",
        "vehicles",
        "fuel_type IS NULL OR (vehicle_type = 'tractor_unit' AND fuel_type IN ('diesel','lng','cng','electric')) OR (vehicle_type IN ('van','passenger_car') AND fuel_type IN ('gasoline','diesel','gas','electric','hybrid'))",
    )
    op.create_check_constraint(
        "vehicle_fuel_tank_capacity_l_applicable",
        "vehicles",
        "fuel_tank_capacity_l IS NULL OR vehicle_type IN ('tractor_unit')",
    )
    op.create_check_constraint(
        "vehicle_fuel_tank_capacity_l_bounds",
        "vehicles",
        "fuel_tank_capacity_l IS NULL OR (fuel_tank_capacity_l > 0 AND fuel_tank_capacity_l <= 10000)",
    )
    op.create_check_constraint(
        "vehicle_fuel_type_applicable",
        "vehicles",
        "fuel_type IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
    )
    op.create_check_constraint(
        "vehicle_identifier",
        "vehicles",
        "(vehicle_type = 'forklift' AND license_plate ~ '^PRIVATE-[0-9A-F]{8}$') OR (vehicle_type <> 'forklift' AND length(trim(license_plate)) > 0 AND license_plate NOT LIKE 'PRIVATE-%')",
    )
    op.create_check_constraint(
        "vehicle_lifting_capacity_kg_applicable",
        "vehicles",
        "lifting_capacity_kg IS NULL OR vehicle_type IN ('forklift')",
    )
    op.create_check_constraint(
        "vehicle_lifting_capacity_kg_bounds",
        "vehicles",
        "lifting_capacity_kg IS NULL OR (lifting_capacity_kg > 0 AND lifting_capacity_kg <= 1000000)",
    )
    op.create_check_constraint(
        "vehicle_max_lift_height_m_applicable", "vehicles", "max_lift_height_m IS NULL OR vehicle_type IN ('forklift')"
    )
    op.create_check_constraint(
        "vehicle_max_lift_height_m_bounds",
        "vehicles",
        "max_lift_height_m IS NULL OR (max_lift_height_m > 0 AND max_lift_height_m <= 100)",
    )
    op.create_check_constraint(
        "vehicle_max_permissible_mass_kg_applicable",
        "vehicles",
        "max_permissible_mass_kg IS NULL OR vehicle_type IN ('tractor_unit', 'container_semi_trailer', 'curtain_side_semi_trailer', 'van')",
    )
    op.create_check_constraint(
        "vehicle_max_permissible_mass_kg_bounds",
        "vehicles",
        "max_permissible_mass_kg IS NULL OR (max_permissible_mass_kg > 0 AND max_permissible_mass_kg <= 1000000)",
    )
    op.create_check_constraint(
        "vehicle_operating_hours_applicable", "vehicles", "operating_hours IS NULL OR vehicle_type IN ('forklift')"
    )
    op.create_check_constraint(
        "vehicle_operating_hours_bounds",
        "vehicles",
        "operating_hours IS NULL OR (operating_hours >= 0 AND operating_hours <= 1000000)",
    )
    op.create_check_constraint(
        "vehicle_payload_capacity_kg_applicable",
        "vehicles",
        "payload_capacity_kg IS NULL OR vehicle_type IN ('container_semi_trailer', 'curtain_side_semi_trailer', 'van')",
    )
    op.create_check_constraint(
        "vehicle_payload_capacity_kg_bounds",
        "vehicles",
        "payload_capacity_kg IS NULL OR (payload_capacity_kg > 0 AND payload_capacity_kg <= 1000000)",
    )
    op.create_check_constraint(
        "vehicle_power_source_applicable", "vehicles", "power_source IS NULL OR vehicle_type IN ('forklift')"
    )
    op.create_check_constraint(
        "vehicle_purchase_price_bounds",
        "vehicles",
        "purchase_price IS NULL OR (purchase_price >= 0 AND purchase_price <= 9999999999.99)",
    )
    op.create_check_constraint(
        "vehicle_required_text",
        "vehicles",
        "length(trim(brand)) > 0 AND length(trim(model)) > 0 AND length(trim(department)) > 0",
    )
    op.create_check_constraint(
        "vehicle_seats_applicable", "vehicles", "seats IS NULL OR vehicle_type IN ('van', 'passenger_car')"
    )
    op.create_check_constraint("vehicle_seats_bounds", "vehicles", "seats IS NULL OR seats BETWEEN 1 AND 100")
    op.create_check_constraint(
        "vehicle_supported_container_sizes_applicable",
        "vehicles",
        "supported_container_sizes IS NULL OR vehicle_type IN ('container_semi_trailer')",
    )
    op.create_check_constraint(
        "vehicle_transmission_applicable",
        "vehicles",
        "transmission IS NULL OR vehicle_type IN ('tractor_unit', 'van', 'passenger_car')",
    )
    op.create_check_constraint("vehicle_year_bounds", "vehicles", "year IS NULL OR year BETWEEN 1900 AND 2100")


def downgrade():
    require_empty()
    op.drop_constraint("vehicle_adblue_capacity_l_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_adblue_capacity_l_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_axle_count_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_axle_count_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_cargo_height_m_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_cargo_height_m_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_cargo_length_m_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_cargo_length_m_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_cargo_width_m_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_cargo_width_m_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_co2_emission_class_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_co2_emission_class_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_container_sizes", "vehicles", type_="check")
    op.drop_constraint("vehicle_current_mileage_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_current_mileage_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_emission_standard", "vehicles", type_="check")
    op.drop_constraint("vehicle_emission_standard_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_empty_weight_kg_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_empty_weight_kg_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_engine_displacement_cc_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_engine_displacement_cc_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_engine_power_hp_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_engine_power_hp_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_fuel_choice", "vehicles", type_="check")
    op.drop_constraint("vehicle_fuel_tank_capacity_l_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_fuel_tank_capacity_l_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_fuel_type_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_identifier", "vehicles", type_="check")
    op.drop_constraint("vehicle_lifting_capacity_kg_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_lifting_capacity_kg_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_max_lift_height_m_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_max_lift_height_m_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_max_permissible_mass_kg_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_max_permissible_mass_kg_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_operating_hours_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_operating_hours_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_payload_capacity_kg_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_payload_capacity_kg_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_power_source_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_purchase_price_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_required_text", "vehicles", type_="check")
    op.drop_constraint("vehicle_seats_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_seats_bounds", "vehicles", type_="check")
    op.drop_constraint("vehicle_supported_container_sizes_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_transmission_applicable", "vehicles", type_="check")
    op.drop_constraint("vehicle_year_bounds", "vehicles", type_="check")
    op.drop_column("vehicles", "vehicle_type")
    op.drop_column("vehicles", "fuel_type")
    op.drop_column("vehicles", "transmission")
    op.drop_column("vehicles", "adblue_capacity_l")
    op.drop_column("vehicles", "axle_count")
    op.drop_column("vehicles", "cargo_height_m")
    op.drop_column("vehicles", "cargo_length_m")
    op.drop_column("vehicles", "cargo_width_m")
    op.drop_column("vehicles", "co2_emission_class")
    op.drop_column("vehicles", "emission_standard")
    op.drop_column("vehicles", "empty_weight_kg")
    op.drop_column("vehicles", "engine_displacement_cc")
    op.drop_column("vehicles", "engine_power_hp")
    op.drop_column("vehicles", "fuel_tank_capacity_l")
    op.drop_column("vehicles", "lifting_capacity_kg")
    op.drop_column("vehicles", "max_lift_height_m")
    op.drop_column("vehicles", "max_permissible_mass_kg")
    op.drop_column("vehicles", "operating_hours")
    op.drop_column("vehicles", "payload_capacity_kg")
    op.drop_column("vehicles", "power_source")
    op.drop_column("vehicles", "supported_container_sizes")
    op.execute("DROP TYPE vehicle_type")
    op.execute("DROP TYPE fuel_type")
    op.execute("DROP TYPE transmission_type")
    op.execute("DROP TYPE power_source")
    postgresql.ENUM("SEDAN", "SUV", "TRUCK", "VAN", "BUS", "MINIVAN", "PICKUP", name="body_type").create(op.get_bind())
    postgresql.ENUM("GASOLINE", "DIESEL", "GAS", "ELECTRIC", "HYBRID", name="fuel_type").create(op.get_bind())
    postgresql.ENUM("MANUAL", "AUTOMATIC", "CVT", "ROBOT", name="transmission_type").create(op.get_bind())
    op.add_column(
        "vehicles", sa.Column("body_type", postgresql.ENUM(name="body_type", create_type=False), nullable=False)
    )
    op.add_column(
        "vehicles", sa.Column("fuel_type", postgresql.ENUM(name="fuel_type", create_type=False), nullable=False)
    )
    op.add_column(
        "vehicles",
        sa.Column("transmission", postgresql.ENUM(name="transmission_type", create_type=False), nullable=False),
    )
    op.add_column("vehicles", sa.Column("engine_volume", sa.Float(), nullable=True))
    op.alter_column("vehicles", "vin", nullable=False)
    op.alter_column("vehicles", "year", nullable=False)
    op.alter_column("vehicles", "current_mileage", nullable=False)
    op.alter_column("vehicles", "department", nullable=True)
