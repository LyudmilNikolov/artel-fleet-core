# EUR-only release deployment

The release supports EUR and Bulgarian/English only. Historical migration files
are intentionally unchanged. The new financial migration locks the affected
tables and rejects **any expense** or any populated purchase price, maintenance
cost, contract/compliance document amount or fuel unit price, including zero. It never converts or
relabels money. The language migration preserves English/Bulgarian preferences,
normalizes removed/unsupported preferences to Bulgarian, and adds a constraint.

The previously inspected development database contains financial values and is
expected to fail this gate. Do not deploy this release over it. A subsequent
reviewed remediation must establish original denominations, approved EUR values,
and retained original values/provenance for every monetary field before an
audited replacement migration path can be authorized. Do not delete records,
clear amounts, use seed scripts, or rewrite uploaded documents to bypass the gate.

## Procedure

1. Back up PostgreSQL and document storage and verify restoration.
2. Build and verify the release against disposable PostgreSQL databases. Migration
   tests create separate random databases and require a test role with CREATEDB.
3. In the release environment, run the read-only preflight before downtime:

   ```sh
   python scripts/check_eur_readiness.py
   ```

   Configure `DATABASE_URL_SYNC` explicitly for the target. The script reports
   revision, enum labels, expense counts by currency and all monetary population
   counts without using the new EUR ORM to decode legacy data. A nonzero exit
   blocks deployment. There is no force option. Leave the current release running.
4. For a financially empty target, stop the app, workers and beat while keeping
   PostgreSQL and infrastructure available. Run the new image's migration once:

   ```sh
   alembic upgrade head
   alembic current
   alembic check
   ```

5. Start all application services on the same new image. Verify enum labels are
   EUR only, server defaults EUR/bg, preferences, both languages, login/logout,
   totals, downloads, exports and logs. Do not mix old and new app images.

Fresh installs run the complete historical chain then both new revisions, with
optional synthetic EUR seed data afterward. Seed ranges are newly generated EUR
examples and do not represent currency conversion.

## Rollback

A rejected migration transaction leaves financial values and schema unchanged.
An empty EUR database can recreate the old enum on downgrade. Downgrade refuses
when any financial record/value exists; it never maps EUR to KZT. Language
normalization cannot recover old preferences. After successful production use,
returning to an old release needs reviewed restoration/reconciliation rather than
a blind downgrade.

## Verification environment

Set `TEST_DATABASE_URL` (asyncpg) and `TEST_DATABASE_URL_SYNC` (psycopg2) to a
separate database whose name includes `test`. Database tests skip when not
configured; a skipped test is not verification of PostgreSQL behavior. Vault is
disabled in the test process. Set `DATABASE_URL` and `DATABASE_URL_SYNC` to these
same disposable URLs for Alembic and preflight commands outside pytest.

```sh
python -m pip install -e '.[dev]'
alembic upgrade head
alembic check
pytest tests/test_i18n tests/test_migrations tests/test_web -q
pytest tests/test_api tests/test_services tests/test_money -q
ruff check app/ scripts/ tests/
ruff format --check app/ scripts/ tests/
```

Review Bulgarian text and datepickers in a browser and download both export
locales before release. Monetary report JSON fields are now decimal strings.
