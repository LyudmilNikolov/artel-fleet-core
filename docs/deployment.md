# Deployment, updates and backups

How to run FleetCore on a server, ship new versions safely (UI only, logic, or together with the database), and
keep backups you can really restore.

Everything here was tried end to end on a throw-away stack: first start on an empty database, backup, restore of
database and files, update to a new version, and rollback.

## 1. Versions

| What | How it is versioned |
|------|--------------------|
| Code | Git. Each release gets a tag `vMAJOR.MINOR.PATCH` (for example `v1.2.0`) |
| Running image | Docker image `fleetcore:v1.2.0`, built from that tag. The server keeps the previous image for rollback |
| Database structure | Alembic revisions in `alembic/versions/`. The current revision is stored in the database (`alembic_version`) |
| Visible version | `GET /api/v1/health` returns `{"status": "ok", "version": "v1.2.0"}` |

Suggested meaning of the numbers: **patch** (`v1.2.1`) = fixes, texts, layout, no database change; **minor**
(`v1.3.0`) = new features, additive database changes; **major** (`v2.0.0`) = breaking changes that need a planned
procedure.

Publish a release from your development machine:

```bash
git tag v1.3.0
git push origin master --tags
```

## 2. What can be updated, and how

The web pages, the CSS, the translations and the business logic are one application, shipped as **one image**. The
web app, the Celery worker and the scheduler (beat) all run that same image, so they are always updated together.
This is deliberate: it removes any "UI is newer than the logic" mismatch.

| Kind of update | Example | Database | What happens | Downtime |
|----------------|---------|----------|--------------|----------|
| **UI only** | Template, CSS, JS, text or translation changes | untouched | New image, containers restart | a few seconds |
| **Logic** | New rule, bug fix, new report | untouched | New image, containers restart | a few seconds |
| **Logic + database** | New table or column, new feature | migrated | Backup, migration, then restart | a few seconds, plus the migration time |

All three use the same command (section 4). `update.sh` always takes a backup first, applies migrations only when
there are some, and checks that the new version answers before it finishes.

**Rules that keep database updates safe**

1. Prefer **additive** changes (new table, new nullable column, new index). The old code keeps working against the
   new structure, so a rollback of the code alone is safe.
2. To remove or rename something, do it in **two releases**: first add the new structure and make the code handle
   both; remove the old one in a later release.
3. **Never edit a migration that was already released.** Add a new one.
4. Some migrations are **guarded on purpose** (the EUR change, the vehicle types change, the driver-documents
   change). They refuse to run on data they cannot convert safely. Follow `docs/eur-deployment.md`,
   `docs/vehicle-workflow.md` and `docs/driver-documents.md` for those; never force them.
5. Before each release run `pytest` and `alembic check` (it must report no new operations) in development, and try
   the release on a restored copy of the production backup (section 6).

## 3. First deployment

On the server (Linux with Docker, Docker Compose v2 and `python3`):

```bash
git clone https://github.com/LyudmilNikolov/fleet-core.git /opt/fleetcore && cd /opt/fleetcore
git checkout v1.0.0

cp .env.production.example .env.production && chmod 600 .env.production
# edit .env.production: passwords, SECRET_KEY, APP_BASE_URL, SMTP, TELEGRAM_BOT_TOKEN, MinIO addresses

docker build -t fleetcore:v1.0.0 --build-arg APP_VERSION=v1.0.0 .
docker compose -f docker-compose.prod.yml --env-file .env.production up -d
docker compose -f docker-compose.prod.yml --env-file .env.production exec -it app python scripts/create_superuser.py
```

The app applies the database migrations itself when it starts. From now on put this in your shell profile on the
server so the scripts use the production files:

```bash
export FLEET_ENV=production
```

**Network.** The production compose file publishes nothing publicly. The app listens on `127.0.0.1:8000` and
MinIO on `127.0.0.1:9000`. Put a reverse proxy with HTTPS in front of both. Example (Caddy):

```
fleet.example.com        { reverse_proxy 127.0.0.1:8000 }
files.fleet.example.com  { reverse_proxy 127.0.0.1:9000 }
```

Browsers download uploaded files straight from the second address, so `MINIO_EXTERNAL_ENDPOINT` must be that public
name and `MINIO_EXTERNAL_USE_SSL=true`. `APP_BASE_URL` is the first address; reminder messages link to it.

**Settings.** Production does not use Vault dev mode. The same variables live in `.env.production` and are passed
to the containers. Keep that file private and out of git (it is ignored). Changing `SECRET_KEY` logs everyone out.

## 4. Updating to a new version

On the server:

```bash
cd /opt/fleetcore
git fetch --tags && git checkout v1.3.0
./scripts/update.sh v1.3.0        # with FLEET_ENV=production exported
```

`update.sh` does, in this order, and stops at the first failure:

1. **Backup** (`pre-update`): database and uploaded files.
2. **Build** the image `fleetcore:v1.3.0`.
3. **Migrate** in a one-off container, before anything is replaced. If a migration fails, the running version is
   untouched and the script says so.
4. **Start** the new version (app, worker, beat).
5. **Health check**: waits until `/api/v1/health` reports the new version.

It remembers the old version in `.deploy-state/previous-version`.

Before updating, read what changed (`git log v1.2.0..v1.3.0 --oneline`), and for a release with database changes
try it on a restored copy first (section 6).

## 5. Rolling back

If the new version misbehaves:

```bash
./scripts/rollback.sh             # back to the previous version (or: ./scripts/rollback.sh v1.2.0)
```

This swaps the **code** only and is safe when the release you are leaving only added things to the database (the
normal case): old code ignores new tables and columns.

If the release changed or removed existing data or structure, go back to the data as well. Restore the
`pre-update` backup taken by `update.sh`, then run the old version:

```bash
ls backups/ | grep pre-update
./scripts/restore.sh --yes backups/fleetcore-db-pre-update-<stamp>.dump backups/fleetcore-files-pre-update-<stamp>.tar.gz
./scripts/rollback.sh v1.2.0
```

Anything entered after the backup was taken is lost by a restore, which is why rollbacks of the code alone are
preferred and why releases should be additive.

## 6. Backups

### What is backed up

| Data | Where it lives | Included |
|------|----------------|----------|
| Everything in the database (vehicles, drivers, documents, expenses, users, rules, reminder log, audit log) | PostgreSQL | yes, `pg_dump` |
| Uploaded files (scans, photos) | MinIO volume | yes, `tar` of the volume |
| Settings and secrets | `.env.production` | **no**: keep a copy in your password manager |
| Redis (job queue) | Redis volume | no, nothing durable is stored there |

A backup is two files that share a timestamp, written to `backups/` (or `$BACKUP_DIR`):

```
fleetcore-db-<label>-<stamp>.dump
fleetcore-files-<label>-<stamp>.tar.gz
```

The database dump is checked for readability before it is kept.

### Schedule and retention (recommended)

Run an automatic backup **every night**. The backups are small (a database of this size is well under a megabyte,
plus your documents), so a daily backup costs almost nothing and means you lose at most one day of work. A weekly
backup would mean up to a week.

```cron
# crontab -e   (as the user that runs Docker)
0 2 * * * cd /opt/fleetcore && FLEET_ENV=production ./scripts/backup.sh auto >> /var/log/fleetcore-backup.log 2>&1
```

Do **not** keep a single backup that you overwrite each time. If the data is damaged and you notice a few days
later, the one backup contains the damage too. Keep a rotation instead. `backup.sh auto` prunes automatically:

| Label | Taken | Kept |
|-------|-------|------|
| `auto` | nightly by cron | the newest 14, plus the oldest backup of each of the last 6 earlier months |
| `pre-update` | by `update.sh` before each release | the newest 5 |
| `manual` | by you: `./scripts/backup.sh manual` | forever (never pruned) |

That is about 20 backups covering roughly 7 months. Change the numbers with
`python3 scripts/prune_backups.py backups/ --keep-auto 30 --keep-monthly 12 --keep-pre-update 10` (use `--dry-run`
to see what would be deleted). Pruning always keeps the database and files of one backup together.

### Keep a copy off the server

A backup on the same disk does not survive a disk failure or a lost server. Set `OFFSITE_COMMAND` and every backup
is copied after it is made. Example with rclone (any cloud, or another server over SSH):

```cron
0 2 * * * cd /opt/fleetcore && FLEET_ENV=production OFFSITE_COMMAND='rclone copy "$BACKUP_DIR" remote:fleetcore-backups' ./scripts/backup.sh auto >> /var/log/fleetcore-backup.log 2>&1
```

### Check that backups can be restored

An untested backup is a guess. Once a month (or after changing the setup):

```bash
./scripts/restore.sh --verify backups/fleetcore-db-auto-<stamp>.dump
```

It restores into a temporary database, prints counts (schema revision, vehicles, drivers, users, documents, audit
entries) and drops the temporary database. The live system is not touched.

### Restoring for real

```bash
./scripts/backup.sh manual          # keep the current state first, unless it is already lost
./scripts/restore.sh --yes backups/fleetcore-db-auto-<stamp>.dump backups/fleetcore-files-auto-<stamp>.tar.gz
```

The database is **replaced**. The app, worker and beat are stopped during the restore and started again at the
end. Passing the files archive is optional; files with the same name are overwritten, extra files are left alone.

### Testing a release on a copy

```bash
./scripts/backup.sh manual
./scripts/restore.sh --verify backups/fleetcore-db-manual-<stamp>.dump
```

For a full rehearsal, do the same on a second machine: restore the backup there, run `update.sh` for the new
version, and click through the main pages before doing it on production.

## 7. Checklist before going live

- [ ] HTTPS reverse proxy in front of the app and of MinIO; the firewall allows only 80/443 (and SSH).
- [ ] Strong, unique values in `.env.production` (database, MinIO, `SECRET_KEY`); the file is `chmod 600`.
- [ ] `APP_BASE_URL`, SMTP and Telegram set; a test message sent from **My channels**.
- [ ] Nightly backup in cron, an off-site copy, and one successful `--verify`.
- [ ] A copy of `.env.production` stored somewhere safe (it is not in the backups).
- [ ] Enough disk space for the database, the MinIO volume and `backups/`; someone looks at it.
- [ ] Operating system and Docker updates scheduled; `docker compose logs` read after each update.
- [ ] Known gap: the session cookie does not set the `Secure` flag yet. Serving only over HTTPS through the proxy
      covers it for now; adding the flag to the session middleware is a small follow-up.

## 8. Command cheat sheet

```bash
export FLEET_ENV=production

./scripts/backup.sh manual                       # backup now
./scripts/restore.sh --verify <dump>             # prove a backup works
./scripts/restore.sh --yes <dump> [files.tar.gz] # replace live data with a backup
./scripts/update.sh v1.3.0                       # backup, build, migrate, start, health check
./scripts/rollback.sh [v1.2.0]                   # previous (or given) version, code only
python3 scripts/prune_backups.py backups/ --dry-run

docker compose -f docker-compose.prod.yml --env-file .env.production ps
docker compose -f docker-compose.prod.yml --env-file .env.production logs -f app
curl -s http://127.0.0.1:8000/api/v1/health      # which version is running
```
