# Documents & Deadlines (Документи и срокове)

Per-vehicle compliance documents with a validity period. This module replaced the
old Contracts module (leasing/rental/warranty/service contracts, payment frequency,
auto-renew and a stored status were removed). Reminders are not implemented yet;
the stored `end_date` is what they will build on.

## Types and applicability

`app/vehicle_rules.py` is the single authority (`ComplianceType`, `COMPLIANCE_TYPES`):

| Vehicle type | Civil liability | CASCO | ГТП (technical inspection) | Tachograph |
|---|---|---|---|---|
| tractor_unit | ✓ | ✓ | ✓ | ✓ |
| container / curtain-side semi-trailer | ✓ | ✓ | ✓ | |
| van, passenger_car | ✓ | ✓ | ✓ | |
| forklift | | | | |

Types are stored as plain strings (`VARCHAR(40)`), so adding a type means editing
`ComplianceType`/`COMPLIANCE_TYPES` and adding `compliance_type.<code>` labels to
both catalogs; no migration. The database does **not** enforce applicability;
`ComplianceService` does, under a `FOR SHARE` lock on the vehicle row. Direct SQL
writes bypass it.

## Data

Table `compliance_documents`: `vehicle_id` (cascade), `document_type`,
`start_date`, `end_date` (inclusive, `>= start_date`), `amount` (EUR,
`NUMERIC(12,2)`, `>= 0`, optional), `provider`, `policy_number`, `notes`,
`renewed_from_id` (set null on delete), `created_by`, timestamps. Scans use the
existing documents feature with `entity_type = compliance_document`.

## Status (computed, never stored)

- `expired`: `end_date` < today
- `expiring_soon`: today ≤ `end_date` ≤ today + `COMPLIANCE_EXPIRING_SOON_DAYS` (config, default 30)
- `valid`: later

"Today" is the UTC date. Per (vehicle, type) the **current** entry is the one with
the latest `end_date` (ties: latest start, newest created). Older entries are
history ("Replaced") and never raise warnings. Dashboard and the daily Celery
count use current entries whose type still applies to the vehicle, excluding
decommissioned vehicles.

If a vehicle's type changes, entries of types that no longer apply are kept as
history, excluded from warnings, and cannot be renewed (edit/delete still work).

## Renewal

"Renew" opens the form with vehicle and type locked, provider prefilled, and a
proposed next period (day after the old end, same length; whole-month periods keep
calendar alignment). The new start may precede the old end. A new entry is created
with `renewed_from_id`; the old entry, its number, amount and scan stay unchanged.
Use Edit only to correct an entry or when the same policy is formally extended.

## Expenses link

A document's cost and the matching expense are one record seen in two places.
Expense categories `civil_liability`, `casco`, `technical_inspection` and `tachograph`
mirror the document types (same codes); `insurance` stays for other insurance.

- A document with an amount automatically gets a linked expense (`compliance_documents.expense_id`,
  unique): category from the type, amount, vendor = provider, description = policy number,
  date = the start date (never later than today, as it is already paid).
- An expense in a document category that carries validity dates (web form: required; API: optional,
  both dates together) creates the linked document too, after checking the type applies to the vehicle.
- Amount and provider/vendor stay in sync both ways. Dates are only edited on the document; the
  expense date only on the expense. A linked expense keeps its category.
- Nothing financial is deleted implicitly: deleting a document keeps its expense; clearing a
  document's amount only unlinks the expense; deleting an expense clears the cost on its document
  (the document stays). Renewing with an amount creates a new expense.

Revision `d7a4f2c61b85` adds the four categories and the link column; its downgrade refuses while
either is in use. Adding a document type later also needs a matching expense category (enum value),
otherwise its cost is recorded under `other`.

## API

`/api/v1/compliance-documents`: `GET` (filters `vehicle_id`, `q`, `document_type`,
`status`, `include_history`, paging), `POST`, `GET/PATCH/DELETE /{id}`,
`POST /{id}/renew`, `GET /types?vehicle_id=`. Responses include `expense_id`. Reads: any authenticated user.
Writes: admin and fleet_manager. PATCH cannot change vehicle or type; omitted
fields are kept and explicit null clears optional fields. Money is serialized as
a decimal string. `/api/v1/contracts` no longer exists.

Document upload and delete (web and API) now require admin or fleet_manager, and
uploads to a compliance entry check that it exists.

## Deployment

Revision `8a3c51d0e7f2` drops `contracts` and renames the `entity_type` label
`CONTRACT` to `COMPLIANCE_DOCUMENT`. It locks the tables and **refuses** to run if
any contract row or contract attachment exists; contract data is never converted.
Downgrade likewise refuses while compliance documents or their attachments exist.

1. Back up the database.
2. `python -m scripts.vehicle_demo_cleanup` (read-only) and
   `python scripts/check_eur_readiness.py`; both work before and after this revision.
3. Confirm `contracts` is empty, stop app/worker/beat, `alembic upgrade head`,
   `alembic check`, restart all services (beat's removed `expire_overdue_contracts`
   task must not stay scheduled).

## Tests

PostgreSQL tests need a disposable database whose name contains `test`:

```sh
export TEST_DATABASE_URL=postgresql+asyncpg://fleetcore@localhost/fleetcore_compliance_test
export TEST_DATABASE_URL_SYNC=postgresql+psycopg2://fleetcore@localhost/fleetcore_compliance_test
VAULT_ADDR= VAULT_TOKEN= DATABASE_URL="$TEST_DATABASE_URL" DATABASE_URL_SYNC="$TEST_DATABASE_URL_SYNC" alembic upgrade head
pytest tests -q
```
