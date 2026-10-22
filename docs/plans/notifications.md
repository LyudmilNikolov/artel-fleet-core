# Plan: notifications (bell, inbox, settings section, email + Telegram reminders)

Status: plan only, nothing built. Written to be pasted into a fresh session. Read `CLAUDE.md` first.
Decisions below were confirmed by the product owner (revision 2).

## Goal

1. A **bell** in the top bar showing documents that need attention.
2. A **Notifications inbox** where every reminder is its own item you can open, stop and step through.
3. A new **sidebar section** to configure rules: which documents, who is notified, which channel, how often, at what time.
4. **Email and Telegram** reminders, for example 30, 15 and 10 days before expiry, then every day (9, 8, 7 ...) until the
   document is renewed or someone presses **Stop**.

## Confirmed decisions

| Topic | Decision |
|---|---|
| Schedule | Milestones (e.g. 30, 15, 10), then a repeat phase every **N** days (default 1: 9, 8, 7 ...), continuing after expiry. Milestones, N and an optional "stop after X days overdue" are configurable per rule. |
| Stop | Any permitted user can press **Stop** on one document (e.g. at 7 days left). It stops reminders for that document for **everyone**, on every channel. It is recorded (who, when), can be **resumed**, and resets by itself when the document is renewed or its end date changes. |
| Message style | **One message per document** (about 20 trucks, so volume is small). Every message is unique and links straight to that item. No digest. |
| Navigation | Each item has its own page with previous/next across the current list. |
| Recipients | **Per rule** (see below). |
| Send time | Default **08:00 Europe/Sofia**, configurable globally, with an optional per-rule override. |
| Channels | Email and Telegram only for now. |
| v1 sources | Vehicle documents (4 types), driver documents (7 types incl. ADR). Service-due items and scheduled maintenance come later (phase 5). |

Also decided:
- **Driver recipients are out of scope for now** (neither the driver concerned nor the vehicle's assigned driver). They
  come later, so keep the recipient `kind` column extensible, but build no UI or sending for them now.
- **Bulgarian time everywhere the user sees or navigates.** All timestamps stay UTC in the database, but "today", days
  left, "next reminder" dates, send history times, the bell, the inbox, the dashboard counts and the dispatcher all use the
  `Europe/Sofia` calendar date, so what the user sees always matches what is sent.
- **Who may press Stop:** admin and fleet_manager (default; viewers and drivers cannot).

## What exists today (verified)

- **Detection already works.** Status of vehicle and driver documents is computed from `end_date`
  (`app/services/validity.py`, `ComplianceService.attention_counts`, `DriverDocumentService.attention_counts`,
  `ServiceDueService`). The dashboard "attention" widget uses it.
- **Nothing is ever sent.** The four daily tasks in `app/tasks/reminders.py` only return counts;
  `check_maintenance_reminders` ends in `# TODO`; `NotificationService.notify_fleet_managers` has no callers.
- **The bell is built but not shown.** `components/notification_bell.html`, `notification_dropdown.html` and
  `app/web/notifications.py` exist, but `base.html` never includes them. They list stored `Notification` rows, an old model
  with types such as `contract_expiry` (the Contracts module is gone).
- **Senders exist.** `app/utils/email.py` (SMTP) and `app/utils/telegram.py` (Bot API), both synchronous and usable from
  Celery. Config: `SMTP_*`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` in `app/config.py` (Vault or env).
- `NotificationPreference` has `email_enabled`, `telegram_enabled`, `telegram_chat_id` per user; no UI uses it.
- Top bar order today: language, dark mode, user menu. The sidebar has an admin-only block.
- Celery beat is static (`crontab(hour=8)` UTC) in `app/tasks/celery_app.py`; tasks use `get_sync_db()`. The container does
  not hot-reload: restart `app`, `celery_worker`, `celery_beat` after changes.

## Design

### A. Attention service (shared by bell, inbox, dashboard, dispatcher)

`app/services/attention_service.py` returns a flat list of items:
`{key, source, entity_type, entity_id, title, subject, end_date, days_left, status, stopped, url}`.
- `key` = `<entity_type>:<entity_id>` and is stable per document entry. A renewed document is a new entry, so a new key.
- Sources come from a **single registry** `app/notification_rules.py` (like `vehicle_rules.py`): every `ComplianceType` as
  `vehicle:<type>` and every driver document type as `driver:<type>`. Labels are `notification_source.<code>` in bg/en. A
  test fails when a source lacks a label or a default rule.
- Reuses the existing "current entry" and status helpers (`current_ids`, `status_clause`); no new status logic. Current
  entries only, active drivers only, decommissioned vehicles excluded, no-expiry never warns.
- Role scope: a driver sees only their own items (`driver_scope`); other roles see all. Sorted: expired first, then fewest
  days left. The dashboard widget switches to counting from this list so numbers never disagree.

### B. Bell and inbox (phase 1, no migration)

- Bell in `base.html`, **between the dark-mode toggle and the user menu**. `GET /notifications/bell`
  (`hx-trigger="load, every 300s"`): red bell icon plus red count badge (`data-bell-tone` still tells expired from only-expiring).
  Dropdown: top items grouped Expired / Expiring soon, each linking to its item page; "view all" opens the inbox.
- **Inbox** `/notifications`: table of all current items with filters (source, vehicle/driver search, status, stopped or
  active) and sort. Replaces the old stored-notifications page. The old `Notification` table and its data are kept.
- **Item page** `/notifications/item/{entity_type}/{entity_id}`: document summary and link to the document, days left,
  rule that applies (milestones, repeat, recipients), **next reminder date**, history of sends, **Stop / Resume** buttons,
  and **Previous / Next** links across the inbox's current filter (carried in the query string). Every email and Telegram
  message links here.
- Never show document numbers (driver numbers are masked for viewers).

### C. Data model (one migration, defaults in code)

- `notification_settings` (single row): `send_time` (default 08:00), `timezone` (default `Europe/Sofia`), `updated_by`.
- `notification_rules`: `source` (unique), `enabled`, `email_enabled`, `telegram_enabled`, `milestone_days` (int[] e.g.
  {30,15,10}; positive, unique, max 365), `repeat_every_days` (int >= 1, nullable = no repeat phase; default 1),
  `stop_after_overdue_days` (nullable), `send_time` (nullable override), timestamps.
  A missing row means code defaults (30/15/10, repeat daily, channels off until configured), so a new document type needs
  no migration.
- `notification_rule_recipients`: `rule_id`, `kind` (string: `user` | `role` | `external_email` | `external_telegram`;
  `driver_self` / `vehicle_driver` are added later without a migration), `user_id` / `role` / `address` as applicable. A rule with no recipients sends nothing and the
  rules page warns about it. Per-user channel details stay in `NotificationPreference` (email on/off, Telegram chat id).
- `notification_stops`: `entity_type`, `entity_id`, `end_date` (snapshot), `stopped_by`, `stopped_at`, `resumed_at`
  (nullable). A stop applies only while its `end_date` equals the document's current one, which gives the reset-on-renewal
  behaviour for free.
- `notification_deliveries` (log and idempotency): `entity_type`, `entity_id`, `end_date`, `channel`, `recipient`
  (user id or external address), `point` (the schedule point that was due, see D), `status` (sent / failed), `error`,
  `created_at`. Unique on `(entity_type, entity_id, end_date, channel, recipient, point)`.
- `notification_runs`: `run_date`, `started_at`, `finished_at`, counts (guards "once per day", see E).

### D. Schedule logic (pure function, the most tested part)

Scheduled **points** are expressed as days-left: the milestones, then, if `repeat_every_days = N`, `last - N, last - 2N, ...`
without a lower bound (so it continues past expiry), cut off at `-stop_after_overdue_days` when set.
Example 30/15/10 with N=1: 30, 15, 10, 9, 8, 7 ... 0, -1, -2 ...
`due_point(days_left, rule, sent_points) -> point | None`:
- The **latest reached point** is the smallest point p with `p >= days_left`. Send when it has no delivery for that
  `(item, channel, recipient)`. If several points were missed (downtime), only the latest one is sent, never a flood.
- A stopped item (current `end_date`) yields nothing. A document created at 12 days left starts at the next point (10).
- A failed send is not recorded as sent, so it retries on the next run.

### E. Dispatcher (Celery) and configurable time

Beat cannot read a time from the database, so beat runs a cheap **tick every 15 minutes**:
`send_due_reminders` converts "now" to the configured timezone (DST-safe with `zoneinfo`), and proceeds only when the
local time is at or past the send time and `notification_runs` has no finished run for today's local date; a per-rule
`send_time` override is checked the same way. Missed runs (downtime) are caught up at the next tick the same day.
For each run:
1. Build attention items, drop stopped ones, apply `due_point`.
2. Resolve recipients per rule (users, roles, external addresses), de-duplicated.
3. Per item, recipient and channel send **one localized message** (recipient's `users.language`, externals use the
   default language): document type, subject (plate or driver name), end date, days left, a link to the item page (which
   offers Stop). Pace Telegram sends (about one per second per chat) and split nothing; messages are short.
4. Write delivery rows (`sent` / `failed`). Email: HTML plus plain text, escaped. Telegram: HTML-escaped.
5. Never include document numbers.
Keep sender calls outside open DB transactions. The old count-only tasks stay until this is verified, then are removed.

### F. Sidebar section and pages

New group in `base.html` (separate from the Admin block), for `admin` and `fleet_manager`: **Известия / Notifications**:
- **Inbox** (the page from B, all roles with scope).
- **Rules** `/settings/notifications/rules`: admin edits, fleet_manager read-only. One row per source: enabled, channels,
  milestones (chip editor), repeat every N days, stop after overdue, send-time override, recipients picker. Live preview
  text such as "30, 15, 10 days before, then every day until renewed". "Apply to all vehicle documents" bulk action.
- **Settings** (admin): default send time and timezone; channel status banners (SMTP / bot token configured or not).
- **My channels**: my email on/off, my Telegram chat id, "send test" buttons.
- **History**: recent deliveries with status and error; filter by channel, status, date.
- All text through i18n (bg + en), audit-log entries for rule changes and every Stop/Resume, CSRF as elsewhere, role
  guards in web and API. Who may press Stop: admin and fleet_manager (confirm if drivers/viewers should).

## Phases (each independently shippable)

1. **Attention service, bell, inbox, item page with Previous/Next**; dashboard counts moved onto the service. No migration.
2. **Registry, rules model, rules page, recipients, settings, sidebar section, My channels** (migration).
3. **Dispatcher**: `due_point`, tick + run guard, delivery log, email + Telegram, Stop/Resume, "send test".
4. **History page, docs** (`docs/notifications.md`), seed defaults, remove the old count-only tasks.
5. Later: planned-service due items and scheduled maintenance as sources, Telegram "Stop" button via bot webhook,
   deep-link Telegram linking.

## Tests (write first)

- `due_point`: table-driven for 30/15/10 + daily: each milestone, 9/8/7, expiry day, overdue, N=2, catch-up after
  missed days (one message only), created late, stop-after-overdue, renewal restarts, stopped and resumed, no end date.
- Attention service: current entries only, renewed/history ignored, terminated drivers and decommissioned vehicles
  excluded, driver scope, stable keys.
- Dispatcher with fake senders and a fake clock: runs once per day, honours timezone and DST, custom and per-rule send
  time, idempotent on re-run, failure retries, recipient resolution and de-duplication, language per recipient, no
  document numbers, escaping, stop prevents sends on all channels.
- Web: bell per role and badge colour, inbox filters, item page Previous/Next order, Stop/Resume permissions and audit
  entry, rules validation (milestones, N, recipients), bg + en strings (existing locale test must stay green).
- Migration test in the style of `tests/test_migrations/`.
- Run against a disposable database as in `docs/driver-documents.md` (`## Tests`).

## Risks and notes

- A stop is global by design; the item page shows who stopped it and when, and Resume is one click.
- Telegram and SMTP credentials come from Vault or env, never stored in the database or shown in the UI.
- Time: one helper (e.g. `sofia_today()` / `to_sofia()` in `app/services/validity.py` or `app/utils/formatting.py`) is the only
  way to get "today" and to display timestamps. Bell, inbox, item page, dashboard attention counts, history and the
  dispatcher all use it, so they agree. Storage stays UTC. Add a test around midnight and the DST change.
- Do not build on the old `Notification` types without checking what still uses them.
