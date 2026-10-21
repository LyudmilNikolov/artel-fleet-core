# FleetCore — Corporate Fleet Management System

Enterprise-grade fleet management system for 500+ vehicles. Self-hosted web application built with Python.

![Dashboard](docs/screenshots/dashboard.png)

## Tech Stack

- **Backend**: FastAPI (async) + SQLAlchemy 2.0 + Alembic
- **Frontend**: Jinja2 + HTMX + Alpine.js + TailwindCSS
- **Database**: PostgreSQL 16
- **Cache/Queue**: Redis 7 + Celery
- **File Storage**: MinIO (S3-compatible)
- **Secrets**: HashiCorp Vault (dev mode)
- **Auth**: JWT + session cookies

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Python 3.12 (conda recommended)

### 1. Start all services

```bash
docker compose up -d
```

This launches PostgreSQL, Redis, MinIO, Vault (dev mode), the app, Celery worker, and Celery beat. Vault automatically generates a random `SECRET_KEY` and seeds all required secrets.

### 2. Run migrations

```bash
docker compose exec app alembic upgrade head
```

### 3. Create superuser

```bash
docker compose exec -it app python scripts/create_superuser.py
```

You will be prompted for username, email, full name, and password (minimum 8 characters).

### 4. Seed demo data (optional)

```bash
docker compose exec app python scripts/seed_data.py
```

Generates 500+ vehicles, 200+ drivers with their documents, maintenance records, expenses, and vehicle documents with deadlines.

### 5. Open the application

Open http://localhost:8000 in your browser.

## Screenshots

| Login | Dashboard |
|:-----:|:---------:|
| ![Login](docs/screenshots/login.png) | ![Dashboard](docs/screenshots/dashboard.png) |

| Vehicles | Drivers |
|:--------:|:-------:|
| ![Vehicles](docs/screenshots/vehicles.png) | ![Drivers](docs/screenshots/drivers.png) |

| Maintenance | Expenses |
|:-----------:|:--------:|
| ![Maintenance](docs/screenshots/maintenance.png) | ![Expenses](docs/screenshots/expenses.png) |

| Contracts | Reports |
|:---------:|:-------:|
| ![Contracts](docs/screenshots/contracts.png) | ![Reports](docs/screenshots/reports.png) |

## Features

### Modules

| Module | Description |
|--------|------------|
| **Vehicles** | Registry of 500+ vehicles with full specs, photos, QR codes |
| **Drivers** | Driver profiles with tabs; ID card, driving licence, driver card, psycho, CPC and medical certificate tracked with validity, renewal and expiry alerts |
| **Mileage** | Mileage logging with anomaly detection |
| **Maintenance** | Scheduled/unscheduled maintenance with Kanban view |
| **Expenses** | Cost tracking by category, fuel consumption analysis |
| **Documents & Deadlines** | Civil liability, CASCO, technical inspection (ГТП) and tachograph validity per vehicle, with renewal and expiry alerts |
| **Documents** | File upload/download via MinIO (S3) |
| **Reports** | TCO, fleet utilization, fuel analysis with Excel/CSV export |
| **Notifications** | In-app, email (SMTP), Telegram alerts |
| **Audit Log** | Complete audit trail of all actions |

### Dashboard

- Fleet overview with vehicle status distribution
- Attention alerts (overdue maintenance, expiring vehicle documents/licenses)
- Expense chart with category breakdown
- Top expensive vehicles
- Maintenance statistics

### Roles

| Role | Access |
|------|--------|
| `admin` | Full system access |
| `fleet_manager` | Vehicle, driver, maintenance, expense, vehicle document management |
| `driver` | View assigned vehicles, submit mileage |
| `viewer` | Read-only dashboards and reports |

### Internationalization

Supported languages: Bulgarian (`bg`, default) and English (`en`).

Browser pages resolve language from a valid `?lang=bg/en`, the signed session,
the authenticated user preference, then Bulgarian. Browser headers do not change
this default. The language switcher saves the selection in the session and user
profile. Anonymous selections survive login, and logout retains the language.
Query overrides affect only the current request.

API requests resolve a valid query override, then supported `Accept-Language`
preferences (including regional tags and quality values), then the authenticated
user preference, then Bulgarian. Only lowercase `bg` and `en` preference writes
are accepted. Background output takes an explicit locale and defaults to Bulgarian.

## API

REST API available at `/api/v1/`:

- `POST /api/v1/auth/login` — authenticate
- `GET /api/v1/vehicles` — list vehicles
- `GET /api/v1/reports/fleet-utilization` — fleet stats
- `GET /api/v1/reports/export/tco.xlsx` — download Excel report
- Full Swagger docs at `/docs`

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

## Development

### Local development (without Docker for app)

```bash
# Create conda environment
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

```bash
# Use an explicitly disposable PostgreSQL database, never development data.
export TEST_DATABASE_URL=postgresql+asyncpg://test_user:password@localhost/fleetcore_test
export TEST_DATABASE_URL_SYNC=postgresql+psycopg2://test_user:password@localhost/fleetcore_test
VAULT_ADDR= VAULT_TOKEN= DATABASE_URL="$TEST_DATABASE_URL" DATABASE_URL_SYNC="$TEST_DATABASE_URL_SYNC" alembic upgrade head
pytest tests/ -v
```

### Run linter

```bash
ruff check app/
```

### Generate migration

```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
```

### Celery worker (for background tasks)

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
├── services/        # Business logic layer
├── repositories/    # Data access layer
├── tasks/           # Celery background tasks
├── templates/       # Jinja2 templates
├── i18n/            # Translation files (bg, en)
└── utils/           # Security, S3, email, export utilities
```

**Pattern**: Routes → Services → Repositories → Database

**Dual auth**: API uses JWT Bearer tokens, Web UI uses session cookies. Both resolve to the same User model.

## Configuration

All secrets are managed via **HashiCorp Vault** (dev mode in Docker Compose). The `vault-init` service automatically seeds:

| Secret | Description |
|--------|-------------|
| `DATABASE_URL` | PostgreSQL async connection string |
| `DATABASE_URL_SYNC` | PostgreSQL sync connection (for Celery) |
| `REDIS_URL` | Redis connection string |
| `SECRET_KEY` | JWT signing key (auto-generated) |
| `MINIO_*` | MinIO S3 endpoint and credentials |
| `SMTP_*` | Email server settings |
| `TELEGRAM_*` | Telegram Bot API settings |

For production, replace Vault dev mode with a proper Vault deployment or inject secrets via your orchestrator.

## License

MIT License. See [LICENSE](LICENSE) for details.
