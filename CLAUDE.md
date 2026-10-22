# CLAUDE.md — Fleet Management System (FleetCore)

## 🎯 Project Overview

Build a **corporate fleet management system** (codename: **FleetCore**) — a self-hosted web application for managing a vehicle fleet of 500+ vehicles. The system should be comparable to Odoo Fleet in functionality but built as a standalone, enterprise-grade Python monorepo application.

**Target users**: Fleet managers, administrators, drivers, and executives at a large enterprise.

---

## 🏗️ Architecture & Tech Stack

### Core Stack (Python-only monorepo)
- **Backend**: FastAPI (async) + SQLAlchemy 2.0 (async ORM) + Alembic (migrations)
- **Frontend**: Jinja2 templates + HTMX + Alpine.js + TailwindCSS (via CDN or pre-built)
- **Database**: PostgreSQL 16
- **Cache / Queue broker**: Redis 7
- **Task Queue**: Celery (async background tasks: notifications, reports, reminders)
- **File Storage**: MinIO (S3-compatible, self-hosted) for document scans and vehicle photos
- **Auth**: JWT tokens (access + refresh) with cookie-based sessions for web UI
- **Containerization**: Docker + Docker Compose (full dev environment in one `docker compose up`)

### Project Structure (monorepo)

```
fleetcore/
├── docker-compose.yml
├── Dockerfile
├── .env.example
├── alembic/
│   ├── alembic.ini
│   └── versions/
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app factory
│   ├── config.py                # Pydantic Settings (env-based config)
│   ├── database.py              # Async SQLAlchemy engine + session
│   ├── dependencies.py          # FastAPI dependency injection
│   │
│   ├── models/                  # SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── vehicle.py
│   │   ├── driver.py
│   │   ├── mileage.py
│   │   ├── maintenance.py
│   │   ├── expense.py
│   │   ├── compliance.py
│   │   ├── document.py
│   │   └── audit_log.py
│   │
│   ├── schemas/                 # Pydantic schemas (request/response)
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── vehicle.py
│   │   ├── driver.py
│   │   ├── mileage.py
│   │   ├── maintenance.py
│   │   ├── expense.py
│   │   ├── compliance.py
│   │   └── common.py            # Pagination, filters, shared schemas
│   │
│   ├── api/                     # REST API endpoints (versioned)
│   │   ├── __init__.py
│   │   ├── v1/
│   │   │   ├── __init__.py
│   │   │   ├── router.py        # Main v1 router aggregator
│   │   │   ├── auth.py
│   │   │   ├── vehicles.py
│   │   │   ├── drivers.py
│   │   │   ├── mileage.py
│   │   │   ├── maintenance.py
│   │   │   ├── expenses.py
│   │   │   ├── compliance.py
│   │   │   ├── reports.py
│   │   │   ├── documents.py
│   │   │   └── users.py
│   │   └── deps.py              # API-specific dependencies
│   │
│   ├── web/                     # Web UI (Jinja2 + HTMX)
│   │   ├── __init__.py
│   │   ├── router.py            # Web routes aggregator
│   │   ├── auth.py
│   │   ├── dashboard.py
│   │   ├── vehicles.py
│   │   ├── drivers.py
│   │   ├── maintenance.py
│   │   ├── expenses.py
│   │   ├── compliance.py
│   │   ├── reports.py
│   │   └── settings.py
│   │
│   ├── services/                # Business logic layer
│   │   ├── __init__.py
│   │   ├── vehicle_service.py
│   │   ├── driver_service.py
│   │   ├── mileage_service.py
│   │   ├── maintenance_service.py
│   │   ├── expense_service.py
│   │   ├── compliance_service.py
│   │   ├── report_service.py
│   │   ├── notification_service.py
│   │   ├── document_service.py
│   │   └── audit_service.py
│   │
│   ├── repositories/            # Data access layer (repository pattern)
│   │   ├── __init__.py
│   │   ├── base.py              # Generic CRUD repository
│   │   ├── vehicle_repo.py
│   │   ├── driver_repo.py
│   │   └── ...
│   │
│   ├── tasks/                   # Celery tasks
│   │   ├── __init__.py
│   │   ├── celery_app.py
│   │   ├── notifications.py     # Email + Telegram notifications
│   │   ├── reminders.py         # Scheduled maintenance/document deadline checks
│   │   └── reports.py           # Async report generation
│   │
│   ├── i18n/                    # Internationalization
│   │   ├── __init__.py
│   │   ├── babel.py             # i18n config + helpers
│   │   ├── ru/
│   │   │   └── messages.json
│   │   ├── kz/
│   │   │   └── messages.json
│   │   ├── en/
│   │   │   └── messages.json
│   │   └── tr/
│   │       └── messages.json
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── security.py          # Password hashing, JWT
│   │   ├── s3.py                # MinIO/S3 client wrapper
│   │   ├── export.py            # Excel (openpyxl) + CSV export
│   │   ├── telegram.py          # Telegram Bot API notifications
│   │   └── email.py             # SMTP email sender
│   │
│   └── templates/               # Jinja2 templates
│       ├── base.html            # Base layout (sidebar, topbar, i18n switcher)
│       ├── components/          # Reusable HTMX partials
│       │   ├── table.html
│       │   ├── modal.html
│       │   ├── pagination.html
│       │   ├── filters.html
│       │   ├── toast.html
│       │   └── stats_card.html
│       ├── auth/
│       │   ├── login.html
│       │   └── profile.html
│       ├── dashboard/
│       │   └── index.html
│       ├── vehicles/
│       │   ├── list.html
│       │   ├── detail.html
│       │   └── form.html
│       ├── drivers/
│       ├── maintenance/
│       ├── expenses/
│       ├── compliance/
│       ├── reports/
│       └── settings/
│
├── static/                      # Static files
│   ├── css/
│   ├── js/
│   └── img/
│
├── tests/
│   ├── conftest.py
│   ├── test_api/
│   ├── test_services/
│   └── test_web/
│
├── scripts/
│   ├── seed_data.py             # Generate demo data (500+ vehicles)
│   └── create_superuser.py
│
├── pyproject.toml               # uv / pip dependencies
├── Makefile                     # Shortcuts: make up, make migrate, make seed, make test
└── README.md
```

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
- Card view option (with photo thumbnail)
- Detail page: vehicle card with tabs (info, mileage history, maintenance, expenses, documents & deadlines, documents)
- HTMX-powered inline editing for quick status changes
- Photo gallery upload (multiple photos per vehicle)
- QR code generation for each vehicle (links to vehicle detail page)

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
- Alert if mileage jump is abnormal (> 1000km/day threshold, configurable)
- Bulk mileage entry form (enter readings for multiple vehicles at once)

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

**Maintenance Schedule Templates:**
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
- `fuel_liters` (float)
- `fuel_price_per_liter` (decimal)
- `fuel_type` (enum)
- `mileage_at_refuel` (integer)

**Reports:**
- Total cost per vehicle (by period)
- Cost breakdown by category (pie chart)
- Fuel efficiency report: L/100km per vehicle
- Monthly cost trends (bar chart)
- Top-10 most expensive vehicles
- Budget vs actual comparison

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
- Driver ↔ Vehicle assignment (one driver can have multiple vehicles, configurable)
- Driver document deadlines (computed status, 30-day window); reminders are not implemented yet
- Driver history: which vehicles they drove and when
- Violation / fine tracking linked to driver

**Driver documents (`driver_documents`, "Лицензи и документи"):** see `docs/driver-documents.md`.
- Types are defined only in `app/driver_document_rules.py` (`id_card`, `driving_licence`, `tachograph_card`,
  `psycho_test`, `professional_competence`, `medical_certificate`); stored as strings, extend without a migration
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
- `document_type` (string from `ComplianceType` in `app/vehicle_rules.py`: civil_liability, casco, technical_inspection, tachograph)
- `start_date`, `end_date` (dates, inclusive, end >= start)
- `amount` (optional EUR Decimal, NUMERIC(12,2), >= 0)
- `provider`, `policy_number`, `notes` (optional)
- `renewed_from_id` (FK to the renewed entry, nullable), `created_by` (FK to User)
- Scans via the documents feature (`entity_type=compliance_document`)

**Rules:**
- Applicability per vehicle type is defined only in `COMPLIANCE_TYPES` (`app/vehicle_rules.py`) and enforced in `ComplianceService`; forklifts have none, only tractor units have tachograph
- Status (valid / expiring soon / expired) is computed from `end_date` with `COMPLIANCE_EXPIRING_SOON_DAYS`, never stored
- Per vehicle and type the entry with the latest `end_date` is current; older entries are history and never warn
- Renew creates a new entry (same vehicle/type, new dates, may overlap); the old entry stays as history
- Reminders are not implemented yet
- A document's amount and its expense are linked (`expense_id`) and synced both ways; document-category expenses (civil_liability, casco, technical_inspection, tachograph) can create the document. Deleting a document never deletes its expense. See `docs/compliance-documents.md`

### 8. 📊 Reports & Dashboards

**Main Dashboard (role-aware):**
- Fleet overview: total vehicles by status (active, maintenance, decommissioned)
- Vehicles requiring attention (upcoming maintenance, expiring or expired vehicle documents)
- Monthly expense summary with trend sparklines
- Mileage statistics (fleet average, top runners)
- Expiring documents widget (licenses, medical certs, insurance)
- Recent activity feed (last 20 actions from audit log)

**Report Types:**
- Vehicle Total Cost of Ownership (TCO) report
- Fleet utilization report (active vs idle vehicles)
- Maintenance history per vehicle / fleet-wide
- Fuel consumption analysis
- Driver performance summary
- Insurance and inspection coverage overview
- Custom date range filtering on all reports

**Export:**
- All reports exportable to Excel (.xlsx) using `openpyxl`; every report has vehicle / vehicle type / date filters shared by page, API and export (see `docs/reports.md`)
- PDF report export is not implemented; do not advertise it
- CSV export for raw data

### 9. 📄 Document Management

**Document model:**
- `id` (UUID)
- `entity_type` (enum: vehicle, driver, maintenance, compliance_document, expense)
- `entity_id` (UUID — polymorphic FK)
- `type` (enum: photo, scan, invoice, act, contract, license, medical, insurance, other)
- `filename` (original filename)
- `s3_key` (path in MinIO)
- `mime_type` (string)
- `size_bytes` (integer)
- `uploaded_by` (FK to User)
- `uploaded_at` (datetime)

**Features:**
- Upload via drag-and-drop in UI
- Image preview in modal
- PDF viewer inline
- MinIO presigned URLs for secure downloads
- Auto-generate thumbnails for photos
- Bulk upload support

### 10. 🔔 Notifications

**Channels:**
- **Email**: SMTP (configurable in settings)
- **Telegram**: Bot API integration (fleet managers get a Telegram group/private notifications)

**Notification Types:**
- Maintenance reminders (upcoming / overdue)
- Vehicle document (insurance, ГТП, tachograph) expiry warnings
- Driver license / medical cert expiry
- Abnormal mileage alerts
- Budget threshold alerts (configurable)
- System notifications (new user registered, etc.)

**Implementation:**
- `NotificationPreference` per user (enable/disable channels per notification type)
- Celery Beat for scheduled checks (run daily at 08:00)
- In-app notification center (bell icon in header, unread count badge)

### 11. 📋 Audit Log

**AuditLog model:**
- `id` (UUID)
- `user_id` (FK)
- `action` (enum: create, update, delete, login, logout, export)
- `entity_type` (string)
- `entity_id` (UUID)
- `changes` (JSONB — diff of old/new values)
- `ip_address` (string)
- `user_agent` (string)
- `timestamp` (datetime)

**Features:**
- Automatic logging via SQLAlchemy events or middleware
- Searchable audit log page (admin only)
- Filter by user, action, entity type, date range
- Cannot be modified or deleted (append-only)
- Retention policy: configurable (default 2 years)

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

```yaml
services:
  app:
    build: .
    ports: ["8000:8000"]
    depends_on: [db, redis, minio]
    env_file: .env
    volumes: ["./app:/app/app"]  # dev hot-reload

  db:
    image: postgres:16-alpine
    volumes: ["pgdata:/var/lib/postgresql/data"]
    environment:
      POSTGRES_DB: fleetcore
      POSTGRES_USER: fleetcore
      POSTGRES_PASSWORD: ${DB_PASSWORD}

  redis:
    image: redis:7-alpine

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]
    volumes: ["minio_data:/data"]

  celery_worker:
    build: .
    command: celery -A app.tasks.celery_app worker -l info
    depends_on: [db, redis]
    env_file: .env

  celery_beat:
    build: .
    command: celery -A app.tasks.celery_app beat -l info
    depends_on: [db, redis]
    env_file: .env

volumes:
  pgdata:
  minio_data:
```

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

## 🚀 Implementation Order (Phases)

### Phase 1 — Foundation
1. Docker Compose setup (all services up and running)
2. FastAPI app scaffold, config, database connection
3. Alembic setup + User model + auth (login, JWT, sessions)
4. RBAC middleware/dependencies
5. Base Jinja2 layout (sidebar, topbar, i18n skeleton)
6. Base CRUD repository pattern

### Phase 2 — Core Modules
7. Vehicle registry (model, API, web UI, CRUD)
8. Driver management (model, API, web UI, CRUD)
9. Mileage tracking (model, API, web UI, bulk entry)
10. Document upload/download (MinIO integration)

### Phase 3 — Business Logic
11. Maintenance module (CRUD + Kanban + Calendar)
12. Expense tracking (CRUD + fuel sub-form)
13. Documents & deadlines (CRUD + renewal + computed expiry status)
14. Audit logging (middleware + admin view)

### Phase 4 — Intelligence
15. Dashboard with charts and widgets
16. Reports with Excel/CSV export
17. Notification system (email + Telegram + in-app)
18. Celery Beat scheduled reminders

### Phase 5 — Polish
19. i18n (Bulgarian and English)
20. Seed data script (500+ realistic vehicles)
21. Tests
22. README + deployment docs

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
- All UI text must go through i18n — no hardcoded strings in templates
- The seed script should generate realistic Kazakh/Russian vehicle data (local license plate formats, local brands popular in KZ like Toyota, Hyundai, Kia, Chevrolet, Lada)
- License plate format for Kazakhstan: `123 ABC 01` (3 digits, 3 letters, 2-digit region code)

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
