# Production on Sevalla: current setup and runbook

What is actually running, and how to operate it. No secrets are in this file: values live only in Sevalla's
environment variables and in the password manager. For the generic step-by-step guide see
[deploy-sevalla.md](deploy-sevalla.md). Update this file whenever the production setup changes.

## What exists (created 2026-10-23)

| Piece | Details |
|-------|---------|
| URL | https://fleetcore-mmzwy.sevalla.app (Sevalla default address, HTTPS by Cloudflare; no custom domain yet) |
| Region | Frankfurt, Germany (`eu-frankfurt-1`, OCI), cluster id `d15a9d2c-398e-42d4-aa75-ef6ff2250c9e` |
| Application | `fleetcore` (`fleetcore-mmzwy`), id `8de89840-adac-4ea6-8b57-8f87d9eb21c3`, GitHub `LyudmilNikolov/artel-fleet-core`, branch **`production`**, build type **Dockerfile**, automatic deploys **off** |
| Web process | id `fa49fcb5-bc3b-4fd2-a785-4ca6469c397a`, size S1, 1 instance, port 8080, start command empty (uses the image: runs migrations, then uvicorn on `$PORT`), readiness/liveness probe `GET /api/v1/health` |
| Worker process | id `d1ae55b8-21f2-460b-964a-8cff4d124d49`, size S1, **exactly 1 instance**, start command `celery -A app.tasks.celery_app worker -B -l info --concurrency 2` (`-B` = the scheduler runs inside it; it must never run twice) |
| PostgreSQL 16 | `fleetcore-db`, id `27896374-cac4-4ed3-9ec8-6ca2568c7431`, size db1 (0.25 GB RAM, 1 GB disk), internal only (external access off) |
| Redis | `fleetcore-redis`, id `31106e5e-1884-4ba4-aed4-3330b7e70984`, size db1 |
| Uploaded files | object storage bucket `fleetcore-files` (`fleetcore-files-qbfuw`), id `6b7b0f6a-f097-4eeb-ac04-20da68be7630`, EU jurisdiction, private (Cloudflare R2) |
| Backups | **nightly database dump** to the separate bucket `fleetcore-backups` (`fleetcore-backups-5bqq8`, id `75a70597-41e4-4460-9b0c-92578af429df`), backup target `nightly-dumps` id `7f984559-1657-4064-8150-772a9c2b0b66`, 01:30 UTC, kept 30 days; manual backups kept forever |

Approximate cost: S1 + S1 + two db1 + storage, about $31 per month.

## Environment variables (names only; set in Sevalla, never in git)

Set by Sevalla when linking the databases: `DATABASE_URL`, `REDIS_URL` (the app accepts the plain `postgresql://` form).

Plain settings: `APP_ENV=production`, `APP_DEBUG=false`, `APP_VERSION`, `APP_BASE_URL`, `TRUST_PROXY=true`,
`GEO_MODE=enforce`, `ALLOWED_COUNTRIES=BG`, `CSP_MODE=enforce`, `VEHICLE_DEPARTMENTS` (JSON list),
`MINIO_ENDPOINT`, `MINIO_EXTERNAL_ENDPOINT` (same host), `MINIO_BUCKET`, `MINIO_USE_SSL=true`, `S3_REGION=auto`.

Secrets (entered by the owner in the dashboard, availability **runtime only**): `SECRET_KEY`, `TELEGRAM_BOT_TOKEN`,
`MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`. Not set yet: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`,
`SMTP_FROM` (email is off until they exist; without `SMTP_HOST` the app shows email as "not configured").

All processes of an application share the same variables. Do **not** set `RUN_MIGRATIONS` (the image migrates only
for the web command). A variable change applies at the next deployment.

## Runbook (with the Sevalla MCP tools)

The MCP server is `https://mcp.sevalla.com/mcp` (OAuth). Tool names below are the ones used in practice.

**Release / redeploy**
1. Code reaches `production` on GitHub (pull request, CI green, merge to `master`, then
   `git checkout production && git merge --ff-only master && git push origin production`).
2. If the release contains a database migration, take a manual backup first (below).
3. `trigger_deployment` (service_type `application`, branch `production`). A restart without rebuilding:
   `restart: true` (use it after changing only variables).
4. `get_deployment` until `success` (about 2 minutes), then `search_runtime_logs` for `startup complete` and
   `Traceback`, and `curl https://fleetcore-mmzwy.sevalla.app/api/v1/health` (shows `APP_VERSION`).
5. Smoke test in the browser (login, save a vehicle, bell). `search_access_logs` with `request_method=POST` shows
   `302` for successful form posts and `422`/`403` for failures.

**Rollback**: `rollback_deployment` to the previous deployment (see `list_deployments`). If the release changed
existing data, restore the manual backup too.

**Manual database backup**: `createDatabaseExternalBackupV3` with `target_id` `7f984559-...` (the database must be
`ready`; creating a backup target briefly puts it in `updating`). List with `listDatabaseExternalBackupsV3`.
Sevalla's own snapshot backups are **not available on this (OCI) cluster**, so only these dumps exist.

**Run a one-off command in the web container**: `execProcessCommandV3` on the web process, command as an argument
list, no shell, 60 s limit, for example `["python","scripts/create_superuser.py","--locale","en","--from-env"]`
(needs `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD` set; remove them afterwards).

**Change a setting**: `set_environment_variables` / `delete_environment_variables`, then redeploy. Do not paste
secret values into a chat; the owner enters them in the dashboard.

**Country restriction check**: set `ALLOWED_COUNTRIES` to another country, restart, confirm the site returns 403
with the "not allowed" page (and `/api/v1/health` stays 200), then set it back to `BG` and restart. This was done on
2026-10-23: the country comes from Cloudflare's `CF-IPCountry` header and cannot be spoofed by the client.

## Lessons learned

- Every web form carries a hidden `csrf_token` field. `app/web/csrf.py` removes it after verification because handlers
  pass the whole form to schemas that forbid unknown fields. When changing form handling, test with the hidden field
  (no header), as `tests/test_web/test_csrf.py` does.
- The dev containers must be restarted to load code changes (no auto-reload); after changing dependencies rebuild:
  `docker compose up -d --build`. The image runs as a non-root user, so use `python -m pytest`.
- Sevalla's default address is `*.sevalla.app`. The database/Redis connection variables are created by Sevalla when
  `create_database` is called with `connect_to_application_id`, so the passwords never pass through a chat.
- The bucket creation API returns the bucket's access key and secret in its response. They were used once for the
  `MINIO_*` variables; rotate them in the dashboard if that exposure matters.

## Open items

- Email (Gmail app password or a mail provider on a custom domain); then add the `SMTP_*` variables.
- Enter the company Telegram group chat ID under *Other Telegram chats* in the rules and press *Apply to all*.
- Rehearse a database restore from a dump once (not tested yet).
- Decide on automatic deploys on push to `production`; tag the live version (`git tag v1.0.1`).
- Admin two-factor login, a role-by-endpoint permission test matrix, check db1 memory use (`get_metrics`), custom domain.
