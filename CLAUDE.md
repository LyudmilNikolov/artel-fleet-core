# CLAUDE.md — Fleet Management System (FleetCore)

## 🎯 Project Overview

**FleetCore** (Bulgarian UI name **Автопарк**) is a corporate fleet management system: a standalone Python monorepo web application (UI + REST API). It is built and running; this file describes the system as it is, so update it whenever behaviour, structure or deployment changes.

**Target users**: fleet managers, administrators, drivers and viewers of a haulage company (tractor units, semi-trailers, vans, cars, forklifts). The live fleet is about 50 vehicles; the design and performance targets are 500+.

**Where things are documented**: feature rules live in `docs/` (`vehicle-workflow.md`, `compliance-documents.md`, `company-licence.md`, `driver-documents.md`, `maintenance-service-items.md`, `reports.md`, `toll-import.md`, `eur-deployment.md`); production setup and runbook in `docs/production-sevalla.md` (generic guide `docs/deploy-sevalla.md`, `docs/deployment.md`). Prefer those docs and the code over this file when they disagree, then fix this file.

---

## 🏗️ Architecture & Tech Stack

### Core Stack (Python-only monorepo)
- **Backend**: FastAPI (async) + SQLAlchemy 2.0 (async ORM) + Alembic (migrations)
- **Frontend**: Jinja2 templates + HTMX + Alpine.js + TailwindCSS (prebuilt `static/css/app.css`; rebuild with `make css`)
- **Database**: PostgreSQL 16
- **Cache / Queue broker**: Redis 7
- **Task Queue**: Celery worker + beat (notifications, reminders)
- **File Storage**: S3-compatible: MinIO in development, Cloudflare R2 (via Sevalla) in production
- **Secrets**: HashiCorp Vault in dev Compose (`vault`, `vault-init`; `app/utils/vault.py`); plain environment variables in production
- **Auth**: JWT tokens (access + refresh) for the API, signed session cookie for the web UI
- **Email**: Brevo API (production; Sevalla blocks SMTP), Gmail API or SMTP (`app/utils/email.py`); **Telegram** Bot API for chat messages
- **Containerization**: Docker + Docker Compose for development (`docker compose up -d`); production runs on **Sevalla** (see `docs/production-sevalla.md`)
- **CI**: GitHub Actions (`.github/workflows/`: `ci.yml`, `release-pr.yml`, `post-deploy-check.yml`)

### Project Structure (monorepo)

```
fleetcore/
├── docker-compose.yml           # dev stack: app, db, redis, minio, vault, celery worker + beat
├── docker-compose.prod.yml      # own-server production stack (docs/deployment.md)
├── Dockerfile, docker-entrypoint.sh   # entrypoint runs migrations for the web command
├── alembic/versions/            # one revision per schema change; never edit historical ones
├── app/
│   ├── main.py                  # app factory, middleware, error pages, template globals
│   ├── config.py                # Pydantic Settings (env-based)
│   ├── database.py, dependencies.py, security.py (geo restriction, headers), audit_context.py, audit_events.py
│   ├── vehicle_rules.py         # vehicle types, technical applicability, COMPLIANCE_TYPES (authoritative)
│   ├── maintenance_rules.py     # planned service items
│   ├── driver_document_rules.py # driver document types
│   ├── notification_rules.py    # reminder sources and schedule rules
│   ├── chart_palette.py
│   ├── models/                  # SQLAlchemy models: user, vehicle, driver, driver_document(+_change), mileage,
│   │                            #   maintenance, expense, compliance, company_licence, document, notification(+_dispatch, _rule),
│   │                            #   toll_import, audit_log
│   ├── schemas/                 # Pydantic v2 request/response schemas
│   ├── api/v1/                  # REST API: auth, users, vehicles, drivers, driver_documents, mileage, maintenance,
│   │                            #   expenses, compliance, documents, reports, plus /health
│   ├── web/                     # Jinja2 + HTMX routes (CSRF on every form): auth, dashboard, vehicles, drivers,
│   │                            #   driver_documents, maintenance, expenses, toll_imports, compliance, company_licence, reports,
│   │                            #   notifications, notification_settings, settings (users, audit log), documents, help
│   ├── services/                # business logic (validity.py = shared computed expiry status)
│   ├── repositories/            # data access (base.py generic CRUD, report_repo.py report queries, ...)
│   ├── tasks/                   # Celery: celery_app.py (beat schedule), notifications.py, reminders.py
│   ├── i18n/                    # locales.py + bg/messages.json + en/messages.json (only these two)
│   ├── utils/                   # s3, export (Excel), email, telegram, plates, formatting, rate_limit, vault, ...
│   └── templates/               # base.html, components/ (HTMX partials), one folder per module
├── static/                      # css (Tailwind input/output), js (app.js), img
├── tests/                       # pytest: test_api, test_services, test_web, test_migrations, test_money, test_i18n, ...
├── scripts/                     # seed_data.py, create_superuser.py, check_eur_readiness.py, gmail_refresh_token.py, ...
├── docs/                        # feature docs, deployment, production runbook
├── testing/                     # manual QA reports and test accounts (not part of the app)
├── toll-export/                 # sample Shell Card exports for the toll importer
├── pyproject.toml, requirements.txt, Makefile
└── README.md
```

### Development commands
- Start: `docker compose up -d`; containers do **not** reload code, so after editing run `docker compose restart app celery_worker celery_beat` (rebuild with `docker compose up -d --build` after dependency changes).
- Tests: run inside the app container with `python -m pytest` (image is non-root) against a disposable PostgreSQL database whose name contains `test`; never against the dev database (see the EUR section below).
- Lint/format: `make lint`, `make format` (Ruff, line length 120). Migrations: `make migrate`, `make migrate-gen msg="..."`.

---

## 📦 Functional Modules — Detailed Specification

### 1. 🔐 Authentication & RBAC

**Roles:**
| Role | Permissions |
|------|------------|
| `admin` | Full access: manage users, system settings, all CRUD operations |
| `fleet_manager` | Manage vehicles, drivers, maintenance, documents & deadlines, expenses, reports |
| `driver` | View assigned vehicles, submit mileage readings, view own maintenance schedule |
| `viewer` | Read-only access to dashboards and reports |

**Requirements:**
- JWT-based auth (access token 30min, refresh token 7d)
- Cookie-based session for web UI (httponly, secure)
- Password hashing with bcrypt
- Login/logout pages with "remember me"
- Profile page (change password, language preference)
- API endpoints return 401/403 with proper error messages
- Permission decorators/dependencies for both API and web routes
- Role-based sidebar menu items (show/hide based on role)

### 2. 🚗 Vehicle Registry (Справочник автомобилей)

**Vehicle model fields:**
- `id` (UUID, PK)
- `license_plate` (гос. номер, unique, string)
- `vin` (optional, unique, trimmed uppercase string, exactly 17 characters when supplied)
- `brand` (марка: Toyota, Hyundai, etc.)
- `model` (модель: Camry, Tucson, etc.)
- `year` (optional manufacturing year, 1900–2100)
- `color` (цвет)
- `vehicle_type` (required: tractor_unit, container_semi_trailer, curtain_side_semi_trailer, van, passenger_car, forklift)
- `fuel_type` (optional and type-specific: gasoline, diesel, gas/LPG, electric, hybrid, lng, cng)
- `engine_displacement_cc` (optional integer cc; applies to tractor units, vans, passenger cars)
- `transmission` (optional: manual, automatic; applies to powered road types)
- `seats` (кол-во мест)
- `purchase_date` (дата приобретения)
- `purchase_price` (стоимость приобретения, decimal)
- `current_mileage` (nullable, authoritative logged reading; only tractor_unit, van, passenger_car)
- `status` (enum: active, in_maintenance, decommissioned, reserved)
- `assigned_driver_id` (FK to Driver, nullable)
- `department` (required; configured VEHICLE_DEPARTMENTS union existing vehicle departments)
- `notes` (примечания, text)
- `photos` (relation to Document, type=photo)
- Timestamps: `created_at`, `updated_at`

**UI Features:**
- Table view with sorting, filtering, search (by plate, VIN, brand, status)
- Card view option (with photo thumbnail) *(not implemented)*
- Detail page: vehicle card with tabs (info, mileage history, maintenance, expenses, documents & deadlines, documents)
- HTMX-powered inline editing for quick status changes
- Photo gallery upload (multiple photos per vehicle)
- QR code generation for each vehicle (links to vehicle detail page) *(not implemented)*

### 3. 📏 Mileage Tracking (Учёт пробега)

**MileageLog model:**
- `id` (UUID)
- `vehicle_id` (FK)
- `recorded_by` (FK to User)
- `value` (integer, km)
- `source` (enum: manual, obd, gps)
- `recorded_at` (datetime)
- `notes` (text, optional)
- `photo_proof` (FK to Document, optional — photo of odometer)

**Business logic:**
- Validation: new mileage value must be >= previous reading for the same vehicle
- Auto-update `vehicle.current_mileage` on new log entry
- Monthly mileage delta calculation
- Average daily/monthly mileage per vehicle
- Alert if mileage jump is abnormal (> 1000km/day threshold, configurable) *(not implemented)*
- Bulk mileage entry (API `POST /mileage/bulk`; no web form)

### 4. 🔧 Maintenance & Inspections (ТО и Техосмотры)

**MaintenanceRecord model:**
- `id` (UUID)
- `vehicle_id` (FK)
- `type` (enum: scheduled_service, repair, inspection, tire_change, body_repair, recall)
- `title` (string — e.g., "ТО-3: замена масла и фильтров")
- `description` (text)
- `status` (enum: scheduled, in_progress, completed, cancelled)
- `scheduled_date` (date)
- `completed_date` (date, nullable)
- `mileage_at_service` (integer)
- `next_service_mileage` (integer, nullable — e.g., next oil change at 60,000 km)
- `next_service_date` (date, nullable)
- `service_item` (string, nullable, planned services only: engine_oil, gearbox_oil, differential_oil, brake_fluid, coolant; none for
  trailers; defined in `app/maintenance_rules.py`) and `service_checklist` (filters ticked under engine oil, not items of their own). The latest completed planned service per vehicle and item is current; its next km / date
  gives a computed overdue / due-soon state (dashboard, vehicle tab, `/maintenance/due`, daily Celery count). See `docs/maintenance-service-items.md`
- `cost` (decimal)
- `service_provider` (string — name of service center)
- `performed_by` (string)
- `documents` (relation — invoices, acts, photos)
- `created_by` (FK to User)

**Maintenance Schedule Templates** *(not implemented)*:
- Predefined service templates (e.g., "ТО каждые 10,000 км or 6 months")
- Auto-generate next maintenance based on template rules
- Kanban board view: Scheduled → In Progress → Completed
- Calendar view of upcoming maintenance

**Reminders (Celery tasks):**
- Notify fleet_manager 14 / 7 / 3 / 1 day before scheduled maintenance
- Notify when vehicle approaches next_service_mileage (within 10,000 km, `SERVICE_DUE_SOON_KM`)
- Notify on overdue maintenance (past scheduled_date but still status=scheduled)

### 5. 💰 Expense Tracking (Расходы)

**Expense model:**
- `id` (UUID)
- `vehicle_id` (FK)
- `driver_id` (FK, nullable)
- `category` (enum: fuel, parts, service, insurance, tax, fine, parking, toll, washing, other)
- `amount` (decimal)
- `currency` (enum: EUR only — default EUR)
- `date` (date)
- `description` (text)
- `receipt_document` (FK to Document, nullable)
- `vendor` (string — supplier/gas station name)
- `created_by` (FK to User)

**Fuel sub-fields (when category=fuel):**
- `fuel_liters` (Decimal, NUMERIC(10,2), > 0)
- `fuel_price_per_liter` (decimal, derived: amount / liters half-up to cents, never entered; recomputed on create and update)
- `fuel_type` (enum)
- `mileage_at_refuel` (integer)

**Reports:**
- Total cost per vehicle (by period)
- Cost breakdown by category (pie chart)
- Fuel efficiency report: L/100km per vehicle
- Monthly cost trends (bar chart)
- Top-10 most expensive vehicles
- Budget vs actual comparison *(not implemented)*

### 6. 👤 Driver Management (Водители)

**Driver model:**
- `id` (UUID)
- `user_id` (FK to User, nullable — if driver has system access)
- `full_name` (string)
- `phone` (string)
- `email` (string, nullable)
- `hire_date` (дата приёма)
- `department` (отдел)
- `status` (enum: active, on_leave, terminated)
- `assigned_vehicles` (relation to Vehicle)
- `photo` (FK to Document)
- `documents` (relation — generic scans, `entity_type=driver`)
- Licence, ID card and similar data live in `driver_documents` (below), not on `drivers`

**Features:**
- Driver ↔ Vehicle assignment: a driver has at most one vehicle (partial unique index on `vehicles.assigned_driver_id`)
- Driver document deadlines (computed status, 30-day window); daily Celery reminders `check_driver_document_expiry`
- Driver history: which vehicles they drove and when
- Violation / fine tracking linked to driver

**Driver documents (`driver_documents`, "Лицензи и документи"):** see `docs/driver-documents.md`.
- Types are defined only in `app/driver_document_rules.py` (`id_card`, `driving_licence`, `tachograph_card`,
  `psycho_test`, `professional_competence`, `medical_certificate`, `adr_certificate`); stored as strings, extend without a migration
- Fields: `driver_id` (FK, cascade), `document_type`, `document_number` (required per type), `categories`
  (driving licence only), optional `start_date`/`end_date` (no end date = no expiry), `notes`, `renewed_from_id`
- Status (valid / expiring soon / expired / no expiry) is computed from `end_date` with
  `DRIVER_DOCUMENT_EXPIRING_SOON_DAYS`, never stored; per driver and type the latest end date is current,
  a renewed entry is never current; only current entries of active drivers warn
- Shares status/renewal helpers with vehicle Documents & Deadlines (`app/services/validity.py`)
- Admin and fleet_manager manage; viewers see masked numbers and no scans; a driver sees only their own record
- Audit entries hold masked numbers only

### 7. 📅 Documents & Deadlines (Документи и срокове)

Replaces the former Contracts module. See `docs/compliance-documents.md`.

**ComplianceDocument model (`compliance_documents`):**
- `id` (UUID), `vehicle_id` (FK, cascade)
- `document_type` (string from `ComplianceType` in `app/vehicle_rules.py`: civil_liability, casco, technical_inspection, tachograph, transport_licence)
- `start_date`, `end_date` (dates, inclusive, end >= start)
- `amount` (optional EUR Decimal, NUMERIC(12,2), >= 0)
- `provider`, `policy_number`, `notes` (optional)
- `renewed_from_id` (FK to the renewed entry, nullable), `created_by` (FK to User)
- Scans via the documents feature (`entity_type=compliance_document`)

**Rules:**
- Applicability per vehicle type is defined only in `COMPLIANCE_TYPES` (`app/vehicle_rules.py`) and enforced in `ComplianceService`; forklifts have none, only tractor units have tachograph and the transport licence copy (the certified copy of the company haulage licence, its own number in `policy_number`)
- Status (valid / expiring soon / expired) is computed from `end_date` with `COMPLIANCE_EXPIRING_SOON_DAYS`, never stored
- Per vehicle and type the entry with the latest `end_date` is current; older entries are history and never warn
- Renew creates a new entry (same vehicle/type, new dates, may overlap); the old entry stays as history
- Daily Celery check `check_compliance_expiry` counts expiring/expired current entries and feeds the reminder rules
- The company's own haulage licence is a separate record, `company_licences` (page `/company-licence`, number, holder, dates, scan, history, dashboard banner when expiring; no bell/email reminders yet). See `docs/company-licence.md`
- A document's amount and its expense are linked (`expense_id`) and synced both ways; document-category expenses (civil_liability, casco, technical_inspection, tachograph) can create the document. Deleting a document never deletes its expense. See `docs/compliance-documents.md`

### 8. 📊 Reports & Dashboards

**Main Dashboard (role-aware):**
- Fleet overview: total vehicles by status (active, maintenance, decommissioned)
- Vehicles requiring attention (upcoming maintenance, expiring or expired vehicle documents)
- Monthly expense summary with trend sparklines
- Mileage statistics (fleet average, top runners)
- Expiring documents widget (licenses, medical certs, insurance)
- Recent activity feed (last 20 actions from audit log)

**Report Types** (see `docs/reports.md`):
- Vehicle Total Cost of Ownership (TCO)
- Fuel consumption analysis (L/100 km, full-to-full)
- Expense analysis (by category and month)
- Maintenance history per vehicle / fleet-wide
- Custom date range, vehicle and vehicle-type filtering on all of them

**Export:**
- Every report exports to Excel (.xlsx) using `openpyxl`; filters are shared by page, API and export
- PDF report export is not implemented; do not advertise it
- Toll expenses can be imported from Shell Card CSV files (`docs/toll-import.md`)

### 9. 📄 Document Management

**Document model:**
- `id` (UUID)
- `entity_type` (enum: vehicle, driver, driver_document, maintenance, compliance_document, expense, company_licence)
- `entity_id` (UUID — polymorphic FK)
- `type` (enum: photo, scan, invoice, act, contract, license, medical, insurance, other)
- `filename` (original filename)
- `s3_key` (path in the bucket)
- `mime_type` (string)
- `size_bytes` (integer)
- `uploaded_by` (FK to User)
- `uploaded_at` (datetime)

**Features:**
- Upload via drag-and-drop in UI, bulk upload
- In-app photo viewer for the gallery, image preview, inline PDF
- Presigned URLs for secure downloads
- Upload and delete require admin or fleet_manager; type and size are validated (`app/utils/upload_validation.py`)

### 10. 🔔 Notifications

**Channels:** email (Brevo / Gmail API / SMTP) and Telegram (chat messages), plus the in-app notification center (bell with unread badge, inbox, per-item pages).

**Sources** (each one a rule in `notification_rules.py`, configured in the UI under notification settings): maintenance reminders (upcoming / overdue), service items due (km or date), vehicle document expiry, driver document expiry.

**Implementation:**
- Settings, one rule per source, and recipients are stored in the database (`notification_settings`, `notification_rules`); dispatch is tracked per item (`notification_dispatch`) so a reminder is not repeated
- Celery Beat (`app/tasks/celery_app.py`): `send_due_reminders` every 15 minutes, the four `check_*` tasks daily at 08:00
- In production the worker runs beat inside (`-B`) with exactly one instance; the app may hibernate and then catches up on wake (`docs/production-sevalla.md`)
- Reminder texts are stored as translation keys and rendered per recipient language

### 11. 📋 Audit Log

**AuditLog model (`audit_logs`):**
- `id` (UUID), `user_id` (FK)
- `action` (create, update, delete, login, logout, export, plus import actions)
- `entity_type` (string), `entity_id` (UUID)
- `changes` (JSONB — diff of old/new values; sensitive numbers are masked)
- `ip_address`, `user_agent`, `timestamp`

**Features:**
- Automatic logging through SQLAlchemy events plus a request context middleware (`audit_events.py`, `audit_context.py`)
- Searchable audit log page (admin only) with filters by user, action, entity type, date range
- Append-only
- No retention/purge job exists yet

### Specified earlier but not implemented
QR codes per vehicle, vehicle card view, kanban and calendar views and maintenance schedule templates (auto-generated next maintenance), abnormal mileage jump alerts, budget alerts and budget-vs-actual, automatic photo thumbnails, audit log retention, fleet utilization / driver performance / coverage reports, PDF export. Do not claim these exist; ask before building them.

---

## 🌐 Internationalization (i18n)

**Supported languages:** Bulgarian (bg, default), English (en)

**Implementation:**
- JSON-based translation files per language
- Jinja2 `_()` function for template translations
- Language switcher in the top navigation bar
- User preference stored in profile (default Bulgarian)
- API responses: error messages localized based on `Accept-Language` header
- Date/number formatting per locale

---

## 🐳 Docker Compose

`docker-compose.yml` is the development stack: `app` (port 8000), `db` (postgres:16-alpine), `redis`, `minio`, `vault` + `vault-init` (seeds all secrets, keeps an existing `SECRET_KEY`), `celery_worker` and `celery_beat`. Volumes: `pgdata`, `minio_data`. Read the file for the details rather than copying it here. `docker-compose.prod.yml` is for a self-run server; the live deployment is on Sevalla (web + one worker process with `-B`, managed PostgreSQL and Redis, R2 bucket).

---

## 🎨 UI/UX Requirements

- **Design system**: TailwindCSS utility classes, clean corporate style
- **Color scheme**: Professional blue/gray palette with colored status badges
- **Layout**: Fixed sidebar (collapsible) + top navigation bar
- **Responsive**: Works on tablet (1024px+), desktop-optimized
- **HTMX patterns**:
  - `hx-get` for loading table data, pagination, filters (no full page reloads)
  - `hx-post` for form submissions
  - `hx-swap="innerHTML"` for table updates
  - `hx-trigger="load"` for lazy-loading dashboard widgets
  - `hx-confirm` for delete actions
- **Alpine.js**: dropdowns, modals, sidebar toggle, form validation
- **Charts**: Chart.js via CDN for dashboard graphs (pie, bar, line, sparklines)
- **Tables**: sortable columns, column visibility toggle, row click → detail page
- **Toasts**: HTMX-powered success/error notifications (top-right corner)
- **Dark mode**: optional toggle (stored in localStorage + user preference)

---

## ⚙️ Non-Functional Requirements

- **Performance**: Handle 500+ vehicles, 1000+ maintenance records, 10,000+ expense records without UI lag. Use pagination (50 items/page default), DB indexes on frequently queried columns.
- **Security**: CSRF protection, input sanitization, SQL injection prevention (ORM), XSS prevention (Jinja2 auto-escape), rate limiting on auth endpoints, secure file upload (validate mime types, max size 10MB).
- **Testing**: pytest + pytest-asyncio + httpx (AsyncClient). Minimum test coverage for all services and API endpoints. Fixtures with factory_boy or custom factories.
- **Code Quality**: Ruff linter + formatter, mypy type hints, pre-commit hooks.
- **Database**: All FKs with proper ON DELETE cascading/SET NULL. UUID primary keys. Proper indexes on: vehicle.license_plate, vehicle.vin, expense.date, maintenance.scheduled_date, audit_log.timestamp.
- **Migrations**: Alembic with auto-generation. Each model change = a migration.

---

## 🚀 Status

All five original phases (foundation, core modules, business logic, intelligence, polish) are delivered, and the app is live. Work now comes as numbered QA issues fixed one commit per step on feature branches (`Fix #NN (k/n): ...`), merged `master` → `production` by the owner. Remaining open items are in `docs/production-sevalla.md` (Open items) and the "Specified earlier but not implemented" list above.

---

## 📝 Code Conventions

- Use `async/await` everywhere (async DB sessions, async API handlers)
- Repository pattern for all DB access (no raw SQL in routes)
- Service layer for business logic (routes → services → repositories)
- Pydantic v2 schemas for all API request/response validation
- Type hints on all function signatures
- Docstrings on all service methods
- Constants in UPPER_SNAKE_CASE in config
- All dates in UTC, convert to user timezone on display
- Money fields as `Decimal` (not float), EUR only, stored as `NUMERIC(12,2)` in DB (`NUMERIC(8,2)` for fuel unit prices). Reject excess precision; report JSON money is serialized as exact decimal strings.

---

## 📌 Important Notes

- This is a **monorepo**: one `pyproject.toml`, one Docker image, one deployment unit
- The web UI and REST API coexist in the same FastAPI app (different route prefixes: `/` for web, `/api/v1/` for API)
- Web UI should feel snappy — leverage HTMX for SPA-like experience without JavaScript frameworks
- All UI text must go through i18n — no hardcoded strings in templates; every key exists in both `bg` and `en`
- All shown times use Europe/Sofia (`APP_TIMEZONE`); storage stays UTC
- Every web form carries a hidden `csrf_token`; `app/web/csrf.py` strips it after verification. Test form handling with the hidden field, as `tests/test_web/test_csrf.py` does
- Working agreement: do not commit or push unless asked (the owner commits and merges); secrets are entered by the owner in the Sevalla dashboard, never pasted into chat; ask before billable or irreversible production actions
- The seed script (`scripts/seed_data.py`) still generates demo data with Kazakhstan-style plates (`123 ABC 01`) and local brands. It is demo data only; the real fleet is Bulgarian, and real road vehicles need a real plate

## EUR and locale deployment constraints

Only `bg` and `en` catalogs may load. Browser language precedence is valid query,
signed session, authenticated user, Bulgarian; browsers ignore Accept-Language.
API precedence is valid query, supported Accept-Language quality preference,
authenticated user, Bulgarian. Locale state is request-local.

Never relabel or convert existing financial data implicitly. Run the read-only
`scripts/check_eur_readiness.py` before deployment; any populated financial value
(including zero) blocks the guarded migration. See `docs/eur-deployment.md`.
Tests require explicit disposable PostgreSQL URLs and must never inherit the
development database. Preserve historical migrations and user-entered content.

### Authoritative vehicle workflow

`app/vehicle_rules.py` defines six independent vehicle types, technical applicability, defaults and bounds. The create/edit form shows both trailer kinds under one Trailer choice with a trailer-kind submenu; this is presentation only, and each kind is still stored as its own type. Technical fields are nullable, Decimal measurements retain exact precision, and incompatible submitted fields are rejected. PATCH omission keeps values, explicit null clears optional fields. Type transitions clear incompatible stored values without applying creation defaults.

Forklifts receive a stable server-generated `PRIVATE-` plus eight hex digits; road types require a real plate. Mileage changes use MileageService under a vehicle row lock, preserve history across type changes, and cannot erase logged readings. Readings never decrease, except a confirmed maintenance correction: a lower mileage entered on a maintenance record (after an explicit overwrite confirmation) adds a reading flagged `is_correction`; it becomes the current mileage and the new baseline, and earlier readings stay as history.

See `docs/vehicle-workflow.md` for API compatibility and guarded deployment. Never run a vehicle migration on retained data without a reviewed mapping strategy.
