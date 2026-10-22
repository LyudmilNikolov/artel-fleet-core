"""Read-only EUR deployment gate. A nonzero exit blocks deployment; no relabeling is safe."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text

from app.config import settings

FINANCIAL_COUNTS_SQL = """
SELECT (SELECT count(*) FROM expenses) AS expenses,
       (SELECT count(*) FROM expenses WHERE fuel_price_per_liter IS NOT NULL) AS fuel_unit_prices,
       (SELECT count(*) FROM vehicles WHERE purchase_price IS NOT NULL) AS purchase_prices,
       (SELECT count(*) FROM maintenance_records WHERE cost IS NOT NULL) AS maintenance_costs
"""
# Amount tables that exist only on some schema revisions (contracts were replaced by compliance documents).
OPTIONAL_AMOUNT_TABLES = {"contract_amounts": "contracts", "compliance_amounts": "compliance_documents"}


def optional_amount_counts(connection) -> dict:
    counts = {}
    for label, table in OPTIONAL_AMOUNT_TABLES.items():
        if connection.execute(text("SELECT to_regclass(:table) IS NOT NULL"), {"table": table}).scalar():
            counts[label] = connection.execute(text(f"SELECT count(*) FROM {table} WHERE amount IS NOT NULL")).scalar()
    return counts


def main() -> int:
    url = settings.DATABASE_URL_SYNC or settings.DATABASE_URL.replace("+asyncpg", "+psycopg2")
    with create_engine(url).connect() as connection:
        # Explicit read-only transaction: this script never decodes legacy enums via ORM.
        connection.execute(text("SET TRANSACTION READ ONLY"))
        counts = dict(connection.execute(text(FINANCIAL_COUNTS_SQL)).mappings().one())
        counts.update(optional_amount_counts(connection))
        result = {
            "revision": connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all(),
            "currency_enum": connection.execute(
                text(
                    "SELECT enumlabel FROM pg_enum JOIN pg_type ON pg_type.oid = enumtypid WHERE typname = 'currency' ORDER BY enumsortorder"
                )
            )
            .scalars()
            .all(),
            "expenses_by_currency": dict(
                connection.execute(text("SELECT currency::text, count(*) FROM expenses GROUP BY currency")).all()
            ),
            "populated_financial_fields": counts,
            "ready": not any(counts.values()),
        }
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not result["ready"]:
        print("Deployment blocked: financial review and an audited remediation plan are required.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
