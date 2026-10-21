# EUR and bg/en verification

Verified on 2026-10-21 on branch `feat/eur-bg-en`. Existing workspace changes
were preserved; no development financial data or uploaded documents were changed.
No release deployment or seed execution was performed.

## Automated checks

- Complete suite: **78 passed** using explicitly configured disposable PostgreSQL
  async/sync test URLs. Vault disabled. Python 3.14 temporary virtual environment;
  bcrypt 4.0.1 matches the existing Dockerfile pin (latest bcrypt is incompatible
  with the repository's existing Passlib integration).
- Full historical migration chain succeeds through `57dd1f893b21` on a fresh
  disposable database. PostgreSQL integration tests independently create/drop
  random test databases. `alembic check` passes. Currency enum/default/NOT NULL,
  language default and existing monetary numeric precision/scale verified.
- Financial gates reject legacy expenses, purchase prices, maintenance costs,
  contract amounts and fuel unit prices, including zero, before financial DDL.
  Failure preserves old revision/enums. Downgrade refuses financial values.
- Read-only readiness script reports head revision, EUR-only enum and ready=true
  on the empty disposable database. Populated development database was not migrated.
- Catalog duplicate/key/placeholder/static and dynamic template coverage passes.
  Decimal HTTP total 0.10 + 0.20 remains the exact JSON string `0.30`.
- XLSX/CSV round trips verify exact amounts, EUR headings, Bulgarian/English labels,
  user text, ISO dates and UTF-8 BOM. Cyrillic document image generation passes
  with a Cyrillic-capable font. Dockerfile installs DejaVu Sans; a release image
  was not rebuilt during this verification.
- `git diff --check` passes. Targeted obsolete-currency searches find only explicit
  rejection/legacy migration tests. No obsolete enum members/global locale state
  remain in active app/scripts.

## Browser inspection

Headless installed Chrome checked **42 page views**, including both languages,
login, profile, lists/forms, notifications, help, dashboard, populated financial
reports, vehicle/maintenance/contract details, and ISO datepicker wire controls.
No JavaScript page errors. Screenshots were visually inspected for Bulgarian
glyphs, translated labels and exact EUR cents. Synthetic test records and user
were removed afterward. Screenshots are temporary artifacts under
`/private/tmp/fleet-eur-browser`.

## Existing lint baseline

Full Ruff check still reports 30 existing findings (enum modernization, generic
syntax, untouched imports and SQLAlchemy boolean comparisons). A comparison against
HEAD found 62 baseline findings and no new file/rule combinations. Changed Python
files are formatted. Full format check reports eight untouched baseline files:
models document/driver/mileage/notification, vehicle repository, notification
service, reminders and S3 utility. These unrelated files were left unchanged.

## Deployment restriction

Follow `docs/eur-deployment.md`. Any populated financial value intentionally blocks
this release's schema migration. A reviewed financial remediation with original
values/provenance and approved EUR values is required before deploying over such
a database. No force option or automatic currency conversion was introduced.
