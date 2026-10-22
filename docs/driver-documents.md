# Driver documents (Лицензи и документи)

Per-driver tracked documents with an optional validity period: ID card, driving licence,
driver (tachograph) card, psychological test, professional competence (CPC) and medical
certificate. It behaves like vehicle [Documents & Deadlines](compliance-documents.md) and shares
its status, renewal and "current entry" helpers (`app/services/validity.py`,
`app/repositories/validity.py`). Reminders are **not** sent yet; the daily Celery task only counts.

## Types

`app/driver_document_rules.py` is the single definition (`DriverDocumentType`, `DRIVER_DOCUMENT_TYPES`):

| Type | Number | Categories |
|---|---|---|
| id_card | required | – |
| driving_licence | required | yes (e.g. `B, C, CE`) |
| tachograph_card | required | – |
| psycho_test | optional | – |
| professional_competence | optional | – |
| medical_certificate | optional | – |
| adr_certificate | optional | – |

Types are stored as plain strings (`VARCHAR(40)`). Adding a type = one entry in the enum and the
table, plus `driver_document_type.<code>` labels in both catalogs; no migration. Templates and
forms always iterate the central table. A test fails when a label is missing.

`categories` is one generic optional column, accepted only where `has_categories` is true,
normalised to `B, C, CE`. `upload_doc_type` is the scan type preselected in the upload form.

## Data

Table `driver_documents`: `driver_id` (FK `drivers`, **ON DELETE CASCADE**), `document_type`,
`document_number`, `categories`, `start_date` ("Issued on"), `end_date` ("Valid until"; empty = no
expiry), `notes`, `renewed_from_id` (set null on delete), `created_by`, timestamps. Check:
`end_date >= start_date` when both are set. Indexes on `(driver_id, document_type, end_date)` and
`end_date`. Scans use the documents feature with `entity_type = driver_document`.

The columns `employee_id`, `license_number`, `license_category`, `license_expiry` and
`medical_expiry` no longer exist on `drivers`.

## Status (computed, never stored)

`expired` (end < today), `expiring_soon` (today ≤ end ≤ today + `DRIVER_DOCUMENT_EXPIRING_SOON_DAYS`,
default 30), `valid`, and **`no_expiry`** (no end date). "Today" is the UTC date.

- Per (driver, type) the **current** entry is the one with the latest end date; an entry without
  end date counts as the latest. Ties: latest start, newest created.
- An entry that has been renewed is never current (so renewing a no-expiry document hands over to
  its successor).
- Only current entries of **active** drivers raise warnings. History never warns; `no_expiry`
  never warns. A missing type shows "Missing" in the tab but is not an alert.

## Renewal, editing, deletion

- **Renew** creates a new entry (same driver and type, `renewed_from_id`); the old one stays as
  history. The form is prefilled with number, categories and notes and a next period of equal length
  (day after the old end). Through the API, number and categories not sent are inherited.
- **Edit** corrects an entry (PATCH: omitted fields kept, explicit null clears; clearing the end date
  makes it "no expiry"). Driver and type are fixed. A required number cannot be cleared.
- **Delete** removes one entry (the previous one becomes current) or, with `scope=all`, the whole
  history of that type, including scans and stored files. Deleting a **driver** removes all their
  entries, attachments and stored files.

## UI

Driver page tabs (like the vehicle page): *General data* (info, assigned vehicles, document alert
summary), *Uploaded documents* (generic gallery, `entity_type=driver`) and *Licences & Documents*
(lazy-loaded; current entry per type plus history; deep link `/drivers/{id}#licences`). The driver
list shows expired / expiring counts and filters by `doc_status`; the dashboard attention widget
counts expired and expiring driver documents next to vehicle ones.

## API

`/api/v1/driver-documents`: `GET` (filters `driver_id`, `document_type`, `status` including
`no_expiry`, `include_history`, paging), `POST`, `GET/PATCH/DELETE /{id}` (`?scope=all`),
`POST /{id}/renew`, `GET /types`. `/api/v1/drivers` responses carry `document_alerts`
(`expired`, `expiring_soon`) and accept `doc_status` on the list.

## Access and personal data

| Role | Entries | Numbers | Scans |
|---|---|---|---|
| admin, fleet_manager | read, create, edit, renew, delete | full | view, upload, delete |
| viewer | read | masked (`•••• 1234`) | no access |
| driver | own record only (`drivers.user_id`) | full (own) | own only |

Creating, editing and deleting drivers in the web UI is restricted to admin and fleet_manager (it
previously only required a login). Scan access is enforced for both `driver` and `driver_document`
attachments in the web and API documents routes.

Create, update, renew and delete of driver documents are written to the audit log
(`entity_type = driver_document`). Document numbers are stored **masked** there (last four
characters), because the log is append-only.

## Reminders

`check_driver_document_expiry` (Celery beat) counts current entries of active drivers that are
expiring soon or expired, per type. Sending notifications is left for the reminders feature.
Restart the worker and beat after deployment; the old task read the dropped columns.

## Deployment

Revision `e3b9c4a71f26` locks `drivers` and `documents`, adds the `DRIVER_DOCUMENT` entity label,
creates `driver_documents`, copies each driver's licence data into one `driving_licence` entry and
`medical_expiry` into one `medical_certificate` entry, then drops the five columns.

`employee_id` has no replacement: the upgrade **refuses** while any value exists, unless run as

```sh
alembic -x drop_legacy_driver_fields=yes upgrade head
```

Downgrade refuses while any driver document or `driver_document` attachment exists, then restores
the columns (empty), the unique constraint and the previous `entity_type` enum.

1. Back up the database.
2. Review `SELECT employee_id FROM drivers WHERE employee_id IS NOT NULL`.
3. Stop app/worker/beat, `alembic upgrade head` (with the flag if you accept dropping the IDs),
   `alembic check`, restart all services.

## Tests

Same disposable PostgreSQL setup as in `compliance-documents.md`. pytest is not installed in the
image by default; install the dev extras in a throwaway container:

```sh
docker compose run --rm --no-deps \
  -e TEST_DATABASE_URL=postgresql+asyncpg://fleetcore@db/fleetcore_driver_docs_test \
  -e TEST_DATABASE_URL_SYNC=postgresql+psycopg2://fleetcore@db/fleetcore_driver_docs_test \
  -e VAULT_ADDR= -e VAULT_TOKEN= -v "$PWD/tests:/app/tests" \
  app sh -c "pip install -q -e '.[dev]' && alembic upgrade head && pytest tests -q"
```

(`DATABASE_URL` must point at the test database for `alembic upgrade head`; see
`tests/conftest.py`, which refuses database names without `test`.)
