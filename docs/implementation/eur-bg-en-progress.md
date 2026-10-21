# EUR-only and Bulgarian/English implementation

User supplied plan is the requirements source. Existing workspace edits are preserved.

- Money: EUR/Decimal schemas, services, exports, guarded migration and preflight delegated.
- Localization: request locale, auth persistence, catalogs and validation delegated.
- UI: templates, web routes, datepickers, seeds, fonts delegated.
- Integration: isolated test configuration, stable service errors, docs and verification.

Ruling: Work on a feature branch in the existing workspace to preserve the user's uncommitted route fixes. No development database migrations or seeds will run.
Ruling: Independent ownership groups share helper interfaces (money(value, lang), render_error(exc, lang), export locale keyword); integration review follows.

Task 1: complete — explicit disposable test configuration; readiness and migration guards verified.
Task 2: complete — request-scoped bg/en resolution and session/auth persistence.
Task 3: complete — Bulgarian catalogs, stable domain errors, validation, template coverage.
Task 4: complete — EUR-only boundaries, Decimal/report serialization, precision validation.
Task 5: complete — forms/displays/charts/datepickers, localized exports and font package.
Task 6: complete — synthetic EUR/localized seed generation and deployment documentation.

Independent reviews found and resolved chart date parsing, export catalog prefixes,
anonymous logout/stale session persistence, seed recipient locales, seed monetary
formatting and missing literal UI labels. Final result: 78 tests pass; 42 populated
browser views without JavaScript errors. See eur-bg-en-verification.md for evidence
and baseline lint limitations. Existing financial deployment blocker remains by design.

## Demo deployment — 2026-10-21

User explicitly confirmed the existing data was disposable and did not need to
be preserved. This supersedes the earlier retention restriction for this demo
instance only. Full database dump saved to
`/private/tmp/fleetcore-before-eur-demo-reset.dump` and its archive listing verified.
Built app/worker/beat images, stopped those services, deleted 2 legacy expenses,
cleared 1 purchase price and 2 maintenance costs (0 populated contract amounts).
Users and nonfinancial rows were preserved. No old monetary value was relabeled.
Preflight then reported ready=true; both migrations succeeded through
`57dd1f893b21`; alembic check detected no pending model changes.
Recreated app/worker/beat with the new images, leaving infrastructure running.
App healthy (HTTP 200), worker ready, beat started. Container checks verified
Bulgarian browser default, explicit English, Decimal EUR display, and actual
installed DejaVu Cyrillic glyph rendering. No seed scripts ran.
