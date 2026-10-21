# Six-type vehicle workflow implementation

Source: user-supplied FleetCore Six-Type Vehicle Workflow Implementation Plan (2026-10-21).

Tasks: rules/cleanup; model/schema/migration; services; HTTP/form; read paths; localization/seeds/docs; verification.

Active database cleanup and deployment are excluded from implementation execution; tooling must be explicit and guarded.

Implemented: shared rules/configuration; nullable six-type model and schemas; empty-table guarded reversible migration; separate snapshot/cleanup CLI; department/identifier/type-transition services; row-locked authoritative mileage and atomic bulk batches; shared API/web validation and raw-value error redisplay; dynamic form; nullable read paths and exact type filtering; report/export compatibility; BG/EN catalogs, seed updates and compatibility/deployment documentation.

Verification so far: migration applied to disposable fleetcore_eur_test; Alembic check found no drift. Full suite reached 109 passing tests; subsequent regression reproduced expired ORM state after a database uniqueness conflict, fixed by refreshing after savepoint rollback. Expanded concurrency/export checks pending.

Baseline lint: unrelated imports, legacy enum style, generic syntax and boolean comparisons remain in unchanged files. Only changed files are formatted/fixed.

Final review: independent reviewer found invalid numeric strings sanitized by browser number inputs. Regression reproduced; rejected numeric controls now render as text inputs, including Alpine technical fields. Added real headless Chromium coverage for defaults, six type switches, disabled controls, draft preservation and raw numeric/null-select redisplay. Corrected browser assertions to wait for Alpine updates.

Verification: 132 tests passed, including live PostgreSQL migration guards, downgrade, cleanup, concurrency, uniqueness retries/recovery, BG/EN parity and Chromium behavior. Final seed/read-path polish pending final rerun.

Completed final verification: **134 passed** (21.88s), including optional installed Playwright/Chromium coverage. `alembic check`: no new upgrade operations. Changed Python files pass Ruff lint and format checks; `git diff --check` is clean. Repository-wide checks retain 25 pre-existing lint findings and 7 formatting findings in unchanged files.

All implementation tasks complete. Active database cleanup/migration, backup restoration, rollout and running seeds were not executed. Configure operator-approved departments and follow docs/vehicle-workflow.md for deployment. Changes remain uncommitted on feat/six-vehicle-types for user review.

## Authorized demo cleanup and active rollout

The user explicitly requested deleting old vehicle records and completing the running deployment.
Root cause of the reproduced GET /vehicles/new 500: the old running Python routes were using the newly mounted template, which requires form_data; active database was still revision 57dd1f893b21. Backed up to /private/tmp/fleetcore-before-six-vehicle-cleanup.dump and restored successfully into fleetcore_vehicle_restore_test_20261021. Stopped app/workers; removed old vehicle demo data using the guarded cleanup script and snapshot /private/tmp/fleet-vehicle-rollout/deleted-vehicles.json; preserved users and drivers. Applied 68ee2a794c32; Alembic check reports no drift. Existing department trucking is configured in ignored local .env and passed to all application services through Compose.

Live verification additionally reproduced POST /api/v1/vehicles with initial mileage failing response serialization because updated_at was expired by the mileage update. Added a failing API regression for create and edited mileage, then explicitly refreshed returned vehicles after successful nested writes. Final full suite: **135 passed** (22.86s). Rebuilt/restarted services. Active HTTP checks now pass API and browser Create/Edit for all six types in BG/EN, exact EUR cents, checkbox selections, stable forklift ID, invalid numeric redisplay (422), archived mileage transitions, and primary pages. Temporary verification records were removed; independently created user records were preserved. App healthy; workers running.
