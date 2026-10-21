"""Readiness/export and separate, explicitly confirmed demo vehicle cleanup.

Default is read-only JSON. Run with --confirm-delete-vehicle-demo and --snapshot
PATH to capture affected IDs/departments before deletion. Back up first. Does
not delete users, drivers, audit history, driver documents, or object storage.
"""

import argparse
import json
from pathlib import Path

from sqlalchemy import create_engine, text

from app.config import settings

# Vehicle-dependent tables; contracts exist before revision 8a3c51d0e7f2, compliance documents after it.
DEPENDENT = [
    ("expense", "expenses"),
    ("maintenance", "maintenance_records"),
    ("contract", "contracts"),
    ("compliance_document", "compliance_documents"),
]


def existing(connection, table):
    return connection.execute(text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}).scalar()


def snapshot(connection):
    entities = {}
    for kind, table in [("vehicle", "vehicles"), *DEPENDENT]:
        if not existing(connection, table):
            continue
        query = (
            f"SELECT id FROM {table}"
            if kind == "vehicle"
            else f"SELECT id FROM {table} WHERE vehicle_id IN (SELECT id FROM vehicles)"
        )
        entities[kind] = [str(v) for v in connection.execute(text(query)).scalars()]
    departments = sorted(
        {
            v.strip()
            for v in connection.execute(text("SELECT DISTINCT department FROM vehicles")).scalars()
            if v and v.strip()
        }
    )
    return {"ready": not entities["vehicle"], "departments": departments, "entities": entities}


def cleanup(connection, captured):
    # Both ID and entity type matter: driver metadata is never included.
    for kind, ids in captured["entities"].items():
        for entity_id in ids:
            connection.execute(
                text("DELETE FROM documents WHERE entity_type::text = :kind AND entity_id = CAST(:id AS uuid)"),
                {"kind": kind.upper(), "id": entity_id},
            )
            connection.execute(
                text("DELETE FROM notifications WHERE lower(entity_type) = :kind AND entity_id = CAST(:id AS uuid)"),
                {"kind": kind, "id": entity_id},
            )
    for table in ["mileage_logs", *(table for _, table in DEPENDENT)]:
        if existing(connection, table):
            connection.execute(text(f"DELETE FROM {table} WHERE vehicle_id IN (SELECT id FROM vehicles)"))
    connection.execute(text("DELETE FROM vehicles"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-delete-vehicle-demo", action="store_true")
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    if args.confirm_delete_vehicle_demo and not args.snapshot:
        parser.error("--snapshot PATH is required for deletion")
    engine = create_engine(settings.DATABASE_URL_SYNC)
    with engine.begin() as connection:
        if args.confirm_delete_vehicle_demo:
            tables = ["vehicles", "mileage_logs", "documents", "notifications"]
            tables += [table for _, table in DEPENDENT if existing(connection, table)]
            connection.execute(text(f"LOCK TABLE {', '.join(tables)} IN ACCESS EXCLUSIVE MODE"))
        captured = snapshot(connection)
        output = json.dumps(captured, ensure_ascii=False, indent=2)
        print(output)
        if args.snapshot:
            # Never overwrite an earlier capture.
            with args.snapshot.open("x") as target:
                target.write(output + "\n")
        if args.confirm_delete_vehicle_demo:
            cleanup(connection, captured)
    engine.dispose()


if __name__ == "__main__":
    main()
