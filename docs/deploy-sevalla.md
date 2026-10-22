# Deploying FleetCore on Sevalla (step by step)

Sevalla builds the app from your GitHub repository with the `Dockerfile`, runs it, and hosts PostgreSQL, Redis
and S3-compatible file storage next to it. This guide takes you from an empty account to a running, locked-down
system. (For a plain Linux server with Docker, see [deployment.md](deployment.md) instead.)

Names of buttons in Sevalla's dashboard can differ slightly from what is written here; the settings themselves are
what matter. Sevalla's own docs: <https://docs.sevalla.com>.

## 0. What you end up with

| Piece | Where it runs | Notes |
|-------|---------------|-------|
| Web app | Sevalla application, web process | Dockerfile build, listens on `$PORT` |
| Reminders and scheduled jobs | Same application, **background worker** process | One instance only (it also runs the scheduler) |
| Database | Sevalla PostgreSQL 16 | Daily automatic backups |
| Queue/cache | Sevalla Redis | |
| Uploaded files | Sevalla object storage (S3-compatible, Cloudflare R2) | |
| HTTPS | Automatic on the default `*.kinsta.app` address | Custom domain later |
| Email | Gmail SMTP with an app password (for now) | |
| Telegram | Your existing bot | |

Estimated cost (Sevalla list prices, excluding tax): web S1 $10 + worker S1 $10 + PostgreSQL $5-34 + Redis $5 +
storage about $1 = **about $30-60 per month**. Check the current prices on sevalla.com before you start; a free
trial exists.

## 1. Before you start (in your repository)

The security and platform changes are in the code. Make sure that is merged and green:

1. The GitHub Actions workflow (`.github/workflows/ci.yml`) passes on `master` (tests, migrations, `alembic check`).
2. Tag the release and create the branch Sevalla will deploy:

   ```bash
   git checkout master && git pull
   git tag v1.0.0 && git push origin v1.0.0
   git branch production master && git push -u origin production
   ```

   Sevalla deploys the **`production` branch**. A release is "move `production` to a tested `master`" (section 12).

3. Generate the secret key once and keep it in your password manager (changing it logs everybody out):

   ```bash
   python3 -c "import secrets; print(secrets.token_hex(32))"
   ```

## 2. Create the Sevalla account and connect GitHub

1. Sign up at sevalla.com (free trial) and create a company/project.
2. Add GitHub as the Git provider and allow it on the `artel-fleet-core` repository (the repository is private).

## 3. Create the database and Redis

Create both in the **same EU region** you will use for the application (internal connections only work inside one
region).

1. **PostgreSQL**, version 16. Start with the smallest size that has at least 1 GB RAM if available; "Database 1"
   (0.25 GB RAM) is very small. You can resize later. Note the **internal connection string**
   (`postgresql://user:password@host:5432/db`).
2. **Redis** (smallest size). Note its internal connection string (`redis://...`).
3. Leave "external connections" **off** for now (you turn them on when you need a manual dump, section 13).

## 4. Create the file storage

1. Create an **object storage** bucket in a European location. Name it `fleetcore`.
2. Create an access key limited to that bucket (read, write, delete).
3. Note the **endpoint host** (for example `<account>.r2.cloudflarestorage.com`), the access key and the secret.

## 5. Create the application

1. New application, source = your GitHub repo, branch **`production`**, build type **Dockerfile**.
2. Region = the same as the database. Pod size for the web process: **S1 (0.5 CPU, 1 GB RAM)**. Do not use Hobby
   (it cannot use a custom domain later and is too small).
3. Health check path: `/api/v1/health`.
4. Link the PostgreSQL and Redis databases (internal connections).
5. Add the environment variables below as **runtime** variables. Sevalla does not read `.env` files, and internal
   connections exist only at runtime (the app applies migrations when it starts, so that is fine).

### Environment variables

| Variable | Value |
|----------|-------|
| `APP_ENV` | `production` (the app refuses to start with a weak production setup) |
| `APP_DEBUG` | `false` |
| `APP_VERSION` | `v1.0.0` (shown by `/api/v1/health`; update it with each release) |
| `SECRET_KEY` | the 64-character key from step 1 |
| `DATABASE_URL` | the PostgreSQL internal connection string (plain `postgresql://...` is fine) |
| `REDIS_URL` | the Redis internal connection string |
| `TRUST_PROXY` | `true` (use the visitor address that Cloudflare/Sevalla forward) |
| `GEO_MODE` | `log` at first, then `enforce` (section 9) |
| `ALLOWED_COUNTRIES` | `BG` (comma-separated, for example `BG,DE`) |
| `MINIO_ENDPOINT` | the storage endpoint host, without `https://` |
| `MINIO_EXTERNAL_ENDPOINT` | the same host (browsers download files from it) |
| `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY` | the bucket key |
| `MINIO_BUCKET` | `fleetcore` |
| `MINIO_USE_SSL` | `true` |
| `S3_REGION` | `auto` |
| `APP_BASE_URL` | the application's address (step 7) |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | Gmail settings (section 8) |
| `TELEGRAM_BOT_TOKEN` | the bare token from @BotFather (no quotes, no brackets) |
| `VEHICLE_DEPARTMENTS` | for example `["Logistics","Workshop"]` |

Tip: put the shared values in a Sevalla **global variable** group, so the web app and the worker use the same ones.

6. Deploy. The first start applies all database migrations. Open the application address Sevalla shows
   (`<name>.kinsta.app`); you should see the login page, and `https://<address>/api/v1/health` shows the version.

## 6. Add the background worker

Reminders and the scheduled checks run here. Add a second **process** of type *background worker* to the same
application (same image), **exactly 1 instance**, pod size S1, with this start command:

```
celery -A app.tasks.celery_app worker -B -l info --concurrency 2
```

`-B` runs the scheduler inside the worker, so no separate "beat" process is needed. Set `RUN_MIGRATIONS=false` on
this process (only the web process migrates). Give it the same variables as the web app.

## 7. Set the public address and create the first admin

1. Copy the application's address (for example `https://fleetcore-xxxx.kinsta.app`) into `APP_BASE_URL` on **both**
   processes and redeploy. Reminder messages link to it.
2. Create the first administrator. Either use Sevalla's terminal/one-off command for the web process:

   ```bash
   ADMIN_USERNAME=boss ADMIN_EMAIL=you@example.com ADMIN_PASSWORD='a-long-password-here' \
     python scripts/create_superuser.py --from-env
   ```

   or run the same command on your own computer against the database (turn on "external connections", put the
   external URL in `DATABASE_URL`, run it, turn external connections off again). The password needs at least 10
   characters. The command does nothing if the user already exists.
3. Log in and create the other users under *Users*.

## 8. Email through Gmail

1. In the Google account that will send the mail, turn on **2-step verification**, then create an **app password**
   (Google Account → Security → App passwords).
2. Set: `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USER=<the gmail address>`,
   `SMTP_PASSWORD=<the 16-character app password>`, `SMTP_FROM=<the same gmail address>`. Redeploy both processes.
3. In the app: *Notifications → My channels → Send test email*.

Limits to know: Gmail caps daily sending, and mail comes from a Gmail address, so it can land in spam. Fine for a
small team. Move to a mail provider with your own domain later (SPF/DKIM).

## 9. Telegram and the country restriction

1. Telegram: the token is already set. Add the bot to your group, then in *My channels* each person enters their
   chat ID (or put the group's ID under *Other Telegram chats* in a rule). Press *Send test Telegram message*.
2. Country restriction (Bulgaria only): `GEO_MODE=log` first. Open the app from your phone and computer, then read the
   application logs: you should see no `no CF-IPCountry header` warnings. If the header is missing, the restriction
   cannot work on this setup. Then set `GEO_MODE=enforce` and redeploy. Someone using a VPN or travelling abroad is
   blocked until you add their country to `ALLOWED_COUNTRIES`. The health check stays reachable from anywhere.

## 10. Smoke test (do this once after the first deploy)

- [ ] Log in; a wrong password five times gives a "too many attempts" message.
- [ ] Create a vehicle and upload a PDF to a document; download it. A renamed `.exe` is refused.
- [ ] Add a vehicle document that expires in a few days; the bell shows it.
- [ ] Create a reminder rule, wait for the next quarter hour after its send time (or use the test buttons).
- [ ] The audit log shows entries in Sofia time.
- [ ] `https://<address>/docs` and `/openapi.json` give "not found".
- [ ] Browser developer tools → the page has the `Content-Security-Policy`, `Strict-Transport-Security` headers.

## 11. Let Claude help operate it (optional)

Sevalla has an official MCP server. In Claude Code run:

```bash
claude mcp add --transport http sevalla https://mcp.sevalla.com/mcp
```

then `/mcp` to log in in the browser. Use a key/role limited to this project, keep Claude's approval prompts on,
and never allow deleting the database or the bucket. Claude can then read logs, check the status of a deployment and
help with releases.

## 12. Releasing an update (the routine)

1. Work on a branch → pull request → **CI must be green** → merge to `master`.
2. Tag it: `git tag v1.1.0 && git push origin v1.1.0`.
3. If the release changes the database (new migration), take a **manual database backup** in Sevalla first.
4. Update `APP_VERSION` to `v1.1.0` on both processes, then move the deploy branch:

   ```bash
   git checkout production && git merge --ff-only v1.1.0 && git push origin production
   ```

   Sevalla builds and deploys. Watch the deployment logs; `/api/v1/health` shows the new version.
5. Roll back: redeploy the previous commit in Sevalla (or `git push --force-with-lease origin <old tag>:production`).
   If a release changed existing data, restore the manual backup as well. Releases that only add things are safe to
   roll back by redeploying.

Dependabot opens weekly pull requests for outdated or vulnerable dependencies; CI runs on each one.

## 13. Backups and moving away later

- Sevalla makes **automatic daily database backups**; confirm in the dashboard how many days are kept.
- Keep an **independent copy** too. Turn on external connections for the database when you need it, then:

  ```bash
  pg_dump -Fc "<external connection URL>" > fleetcore-$(date +%F).dump
  rclone sync <bucket remote>:fleetcore ./files-backup        # uploaded files
  ```

  Do it weekly (or before risky releases) and store it somewhere other than Sevalla.
- Practice a restore once: `createdb` a test database and `pg_restore --no-owner -d <test db> fleetcore-<date>.dump`
  (or use `./scripts/restore.sh --verify <dump>` against a local Docker stack, see [deployment.md](deployment.md)).
- The dump format is the standard PostgreSQL custom format, so moving to another host later is a restore plus
  copying the bucket.

## 14. Later (not needed for launch)

- Buy a domain and add it as a custom domain (free SSL; not available on the Hobby pod), update `APP_BASE_URL`.
  Add a mail provider with SPF/DKIM on that domain.
- Two-factor login for administrators (TOTP).
- Clean up the existing `ruff` findings and make the lint step in CI blocking.

## Security summary (what the app does by itself)

- No self-registration; users are created by administrators only. Passwords of at least 10 characters.
- Login throttling per user and address (5 failures / 15 minutes, then a wait), CSRF protection on all forms and
  HTMX requests, a new session on every login, sessions and API tokens end when the password changes, 12-hour sessions.
- Security headers (HSTS, CSP, frame/sniffing protection), hidden API documentation, secure cookies in production.
- Upload checks by file content, size limit, safe filenames; Office files download as attachments.
- Optional country restriction; the container runs as an unprivileged user; dependency versions are pinned and
  checked weekly.
- The app refuses to start in production without a strong `SECRET_KEY` or with debug on.
