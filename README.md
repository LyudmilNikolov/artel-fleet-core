# FleetCore — Corporate Fleet Management System

Self-hosted web application for managing a corporate vehicle fleet (500+ vehicles): vehicles, drivers, mileage,
maintenance, expenses, vehicle and driver document deadlines, reports, and email / Telegram reminders. Built with
Python. The interface is Bulgarian (default, shown as **Автопарк**) and English.

## Tech Stack

- **Backend**: FastAPI (async) + SQLAlchemy 2.0 + Alembic
- **Frontend**: Jinja2 + HTMX + Alpine.js + TailwindCSS (prebuilt `static/css/app.css`)
- **Database**: PostgreSQL 16
- **Cache/Queue**: Redis 7 + Celery (worker + beat)
- **File Storage**: MinIO (S3-compatible)
- **Secrets**: HashiCorp Vault (dev mode in Docker Compose)
- **Auth**: JWT (API) + session cookies (web)

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12 only if you run the app outside Docker

### 1. Start all services

```bash
docker compose up -d
```

This launches PostgreSQL, Redis, MinIO, Vault (dev mode), the app, the Celery worker and Celery beat. The
`vault-init` service seeds every secret into Vault, including a random `SECRET_KEY` (an existing key is kept when
you re-run it, so logged-in sessions survive). The app container applies database migrations itself on start.

The containers do **not** reload code on change. After editing code or templates, restart the services:

```bash
docker compose restart app celery_worker celery_beat
```

### 2. Create a superuser

```bash
docker compose exec -it app python scripts/create_superuser.py
```

You will be prompted for username, email, full name and password (minimum 8 characters).

### 3. Seed demo data (optional)

```bash
docker compose exec app python scripts/seed_data.py
```

Generates 500+ vehicles, 200+ drivers with their documents, maintenance records, expenses, and vehicle documents
with deadlines.

### 4. Open the application

Open http://localhost:8000 in your browser.

## Features

### Modules

| Module | Description |
|--------|------------|
| **Vehicles** | Six vehicle types (tractor unit, container semi-trailer, curtain-side semi-trailer, van, passenger car, forklift) with type-specific fields, status, assigned driver, photo gallery. See [vehicle workflow](docs/vehicle-workflow.md) |
| **Drivers** | Driver profiles and logins; ID card, driving licence, driver (tachograph) card, psychological test, CPC, medical certificate and ADR tracked with validity, renewal and expiry status. Drivers can propose changes that a manager approves. See [driver documents](docs/driver-documents.md) |
| **Mileage** | Readings per vehicle (a reading never goes below the last one, except a confirmed maintenance correction); history on the vehicle page, bulk entry through the API |
| **Maintenance** | Scheduled and unscheduled maintenance; planned service items (engine oil, gearbox oil, differential oil, brake fluid, coolant) with overdue / due-soon tracking. See [service items](docs/maintenance-service-items.md) |
| **Expenses** | Cost tracking by category in EUR, fuel liters with derived price per liter, links to vehicle documents. |
| **Documents & Deadlines** | Civil liability, CASCO, technical inspection (ГТП) and tachograph validity per vehicle, with renewal and computed expiry status. See [compliance documents](docs/compliance-documents.md) |
| **Documents** | File upload / download through MinIO (S3) |
| **Reports** | TCO, fuel consumption, expense analysis and maintenance history with shared filters and Excel / CSV export. See [reports](docs/reports.md) |
| **Notifications** | Bell, inbox and per-document pages; configurable reminder rules sent by email and Telegram (see below) |
| **Audit Log** | Append-only trail of create / update / delete / login / export actions (admin only) |

### Dashboard

- Fleet overview with vehicle status distribution
- Attention alerts (overdue and upcoming maintenance, planned services due, expiring or expired vehicle and driver documents)
- Expense chart with category breakdown
- Top expensive vehicles and maintenance statistics

### Notifications and reminders

**What needs attention** is computed from the data, never stored: the current vehicle document (civil liability,
CASCO, technical inspection, tachograph) and driver document of each type that is expired or expiring soon.
Documents without an end date, renewed or replaced entries, terminated drivers and decommissioned vehicles never
warn.

- **Bell** (top bar, always red) with the number of documents needing attention, a dropdown with the most urgent
  ones, and an **inbox** (`/notifications`) with filters, sorting and a *Messages* tab for system messages.
- **Item page** per document with Previous / Next, the schedule, the next reminder date, the send history and
  **Stop / Resume** (admin and fleet manager). A stop applies to everyone and every channel, is audited, and resets
  when the document is renewed or its end date changes.
- **Rules** (sidebar → Notifications → Rules): per document type, the schedule (milestones such as 30, 15, 10 days,
  then a repeat every N days that continues after expiry, with an optional stop after N days overdue), channels
  (email, Telegram), an optional send time, and recipients (users, roles, extra email addresses, extra Telegram
  chats). The rules, settings and channels are **administrator-only**; fleet managers only use the bell and the inbox. Until a rule is saved nothing is sent.
- **Settings** (admin): default send time (08:00) and time zone, and whether email / Telegram are configured on
  the server. **My channels** (admin): your own email on/off and Telegram chat ID, and "send test" buttons.
- **Sending**: Celery beat runs a tick every 15 minutes. Each rule sends once per local day at its send time, one
  message per document, recipient and channel, in the recipient's language, always linking to the item page.
  After downtime only the latest due message is sent. Messages never contain document numbers. Every send is
  logged and is idempotent, and a failed send is retried at the next run.
- Telegram is a global channel: the administrator puts the company group's chat ID under *Other Telegram chats* in the
  rules (use "Apply to all" to copy it), and nobody else has to set anything up. Email goes to the address on each
  recipient's account or to extra addresses typed into a rule.
- `POST /api/v1/auth/login` — authenticate
- `GET /api/v1/vehicles` — list vehicles
- `GET /api/v1/compliance-documents`, `GET /api/v1/driver-documents` — document deadlines
- `GET /api/v1/reports/fleet-utilization` — fleet stats
- `GET /api/v1/reports/export/tco.xlsx` — download Excel report
- Swagger docs at `/docs` (available while `APP_DEBUG` is on)

There is no notifications API; reminders are configured in the web interface.

### Currency and exact money

All new financial records use EUR exclusively. Omitted expense currency defaults
to `EUR`; other codes, lowercase variants and explicit null currency are rejected
on create and update. Monetary values use Decimal with the existing database
precision: 12 digits including 2 decimals, or 8 including 2 for fuel unit prices.
Excess precision is rejected. Report JSON monetary fields are exact decimal
strings (an intentional API change); consumers must parse them as decimal values.
Charts use numeric coordinates only for drawing and preserve exact amounts for
labels. Pages display two decimals and the `EUR` code in either locale.

Excel and BOM-prefixed UTF-8 CSV exports have localized headings and enum labels.
CSV amounts remain decimal strings and dates remain ISO. Excel money cells use
EUR formatting; values beyond Excel's reliable precision are stored as exact text.
PDF report export is not implemented.

Existing installations must follow [the EUR deployment procedure](docs/eur-deployment.md)
before running migrations. Financial data blocks the schema transition until a
separately reviewed remediation is approved. There is no automatic conversion.

## Configuration

Secrets and settings are managed through **HashiCorp Vault** (dev mode in Docker Compose). The `vault-init`
service seeds them on every `docker compose up`, taking the values below from the **root `.env` file** (Docker
Compose reads it automatically; `.env` is git-ignored). The app, worker and beat containers read them from Vault.

| Setting | Description |
|--------|-------------|
| `DATABASE_URL`, `DATABASE_URL_SYNC`, `REDIS_URL` | Connection strings (seeded by default) |
| `SECRET_KEY` | JWT / session signing key (generated once, kept on re-seed) |
| `MINIO_*` | MinIO S3 endpoint and credentials |
| `APP_BASE_URL` | Public address of the web UI, used for the links in reminder messages (default `http://localhost:8000`; set it to the address people actually open) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Email server; until `SMTP_HOST` is set, email reminders are skipped |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token; until set, Telegram reminders are skipped |
| `APP_ENV` | `production` makes the app refuse to start with a weak `SECRET_KEY` (under 32 characters), debug on, or no database |
| `TRUST_PROXY` | `true` behind Cloudflare / a platform proxy: use the visitor address from `CF-Connecting-IP` / `X-Forwarded-For` |
| `GEO_MODE`, `ALLOWED_COUNTRIES` | Country restriction using Cloudflare's `CF-IPCountry` header: `off`, `log` (only log) or `enforce` (403); default list `BG` |
| `CSP_MODE` | `enforce` (default), `report-only` or `off` for the Content-Security-Policy header |
| `S3_REGION`, `MINIO_EXTERNAL_USE_SSL` | Object storage region (`auto` for Cloudflare R2) and https for browser download links |
| `RUN_MIGRATIONS` | `true`/`false` overrides the automatic choice (migrations run only for the web command) |

Example `.env` lines (the token is the bare value from @BotFather, without quotes or brackets):

```
TELEGRAM_BOT_TOKEN=123456:ABC-your-token
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=alerts@example.com
SMTP_PASSWORD=secret
SMTP_FROM=alerts@example.com
APP_BASE_URL=https://fleet.example.com
```

After changing `.env`, re-seed Vault and restart the services:

```bash
docker compose up -d --no-deps vault-init
docker compose restart app celery_worker celery_beat
```

Reminder settings that are not secrets (send time, time zone, rules, recipients) are configured in the web
interface under **Notifications**. Credentials are never shown there.

For production, replace Vault dev mode with a proper Vault deployment or inject secrets through your orchestrator.

## Development

### Local development (without Docker for the app)

```bash
# Create a Python 3.12 environment, for example with conda
conda create -n fleet-core python=3.12 -c conda-forge
conda activate fleet-core
pip install -e ".[dev]"

# Start infrastructure only
docker compose up -d db redis minio vault vault-init

# Run migrations
alembic upgrade head

# Start the app with hot-reload
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Run tests

Tests need an explicitly disposable PostgreSQL database and refuse database names without `test`. Never point them
at development data.

```bash
export TEST_DATABASE_URL=postgresql+asyncpg://test_user:password@localhost/fleetcore_test
export TEST_DATABASE_URL_SYNC=postgresql+psycopg2://test_user:password@localhost/fleetcore_test
VAULT_ADDR= VAULT_TOKEN= DATABASE_URL="$TEST_DATABASE_URL" DATABASE_URL_SYNC="$TEST_DATABASE_URL_SYNC" alembic upgrade head
pytest tests/ -v
```

Inside Docker Compose, using a throwaway database, see the `## Tests` section of
[driver documents](docs/driver-documents.md).

### Run linter

```bash
ruff check app/ tests/
```

### Generate migration

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic check   # must report no new operations
```

### Frontend CSS

`static/css/app.css` is built with Tailwind CSS 3 and committed. After using a Tailwind class that is not in the
file yet (in a template or in `static/js/app.js`), rebuild it:

```bash
make css
```

### Celery

The worker registers the tasks in `app/tasks/reminders.py` and `app/tasks/notifications.py`. Beat runs the reminder
tick every 15 minutes and the older daily count tasks at 08:00 UTC.

```bash
celery -A app.tasks.celery_app worker -l info
celery -A app.tasks.celery_app beat -l info
```

## Architecture

```
app/
├── api/v1/          # REST API endpoints
├── web/             # Web UI routes (Jinja2 + HTMX)
├── models/          # SQLAlchemy ORM models
├── schemas/         # Pydantic request/response schemas
├── services/        # Business logic (attention list, reminder schedule, dispatcher, rules, ...)
├── repositories/    # Data access layer
├── tasks/           # Celery background tasks
├── templates/       # Jinja2 templates
├── i18n/            # Translation files (bg, en)
└── utils/           # Security, S3, email, Telegram, export, formatting utilities
```

**Pattern**: Routes → Services → Repositories → Database

**Dual auth**: API uses JWT Bearer tokens, the web UI uses session cookies. Both resolve to the same User model.

**Single sources of truth**: vehicle types and their rules in `app/vehicle_rules.py`, driver document types in
`app/driver_document_rules.py`, planned service items in `app/maintenance_rules.py`, notification sources and
defaults in `app/notification_rules.py`. Adding a type means one entry there plus translations; no migration.

## Security

- No self-registration (administrators create users), passwords of at least 10 characters, one password policy for
  the API, web forms and the CLI.
- Login throttling per user and address (429 with `Retry-After`), CSRF protection for the cookie-based web UI, a new
  session on every login, 12-hour sessions, and sessions/tokens end when the password changes.
- Security headers (HSTS in production, CSP, frame and sniffing protection), hidden API docs outside debug,
  `Secure` cookies in production, content-based upload checks with safe filenames and attachment downloads for
  Office files.
- Optional country restriction (`GEO_MODE`), non-root container, pinned dependencies (`requirements.txt`),
  Dependabot and a CI workflow (tests, migrations, `alembic check`, dependency audit).

## Deployment and updates

Two supported routes: **Sevalla** (managed platform, GitHub → CI → automatic deploy; see
[docs/deploy-sevalla.md](docs/deploy-sevalla.md)) or a plain Linux server with Docker (below).

Production uses `docker-compose.prod.yml` and a private `.env.production` (no Vault dev mode). Releases are git
tags (`v1.2.0`) built into one image, shown by `GET /api/v1/health`. UI, logic and database changes all ship the
same way, and scripts handle the safe order:

```bash
export FLEET_ENV=production
./scripts/update.sh v1.3.0     # backup, build, migrate, start, health check
./scripts/rollback.sh          # back to the previous version
./scripts/backup.sh manual     # database + uploaded files
./scripts/restore.sh --verify <dump>   # prove a backup can be restored
```

Nightly backups (cron) are rotated automatically: the newest 14 plus one per month for 6 months. See
[the deployment guide](docs/deployment.md) for the first deployment, the HTTPS proxy, off-site copies and the
checklist.

## More documentation

| Document | Topic |
|----------|-------|
| [deploy-sevalla.md](docs/deploy-sevalla.md) | Step-by-step deployment on Sevalla (database, storage, worker, Gmail, Telegram, country restriction, updates) |
| [deployment.md](docs/deployment.md) | Production deployment, updates, rollback, backups and restore |
| [vehicle-workflow.md](docs/vehicle-workflow.md) | Vehicle types, mileage rules, guarded deployment |
| [compliance-documents.md](docs/compliance-documents.md) | Vehicle documents and deadlines |
| [driver-documents.md](docs/driver-documents.md) | Driver documents, access rules, test setup |
| [maintenance-service-items.md](docs/maintenance-service-items.md) | Planned service items and due tracking |
| [reports.md](docs/reports.md) | Reports, filters and exports |
| [eur-deployment.md](docs/eur-deployment.md) | EUR-only money and the guarded migration |
| [plans/notifications.md](docs/plans/notifications.md) | Design of the notification system |

## License

MIT License. See [LICENSE](LICENSE) for details.
