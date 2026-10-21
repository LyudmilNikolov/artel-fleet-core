# Six-type vehicle workflow

The browser Create/Edit form and REST API share `app/vehicle_rules.py`. Types are
`tractor_unit`, `container_semi_trailer`, `curtain_side_semi_trailer`, `van`,
`passenger_car`, and `forklift`. Van and Forklift are independent types.

Configure `VEHICLE_DEPARTMENTS` as a JSON array of approved business names.
Existing nonblank vehicle departments remain available. An empty union prevents
creation. Authenticated clients can discover choices at
`GET /api/v1/vehicles/departments`.

## API compatibility

Create requires an explicit `vehicle_type`, brand, model and department. Road
types require a plate; Forklift Create accepts omitted/null/blank plate and
rejects supplied nonblank identifiers. The server generates its internal ID.
Forklift edits may omit the ID or supply the unchanged ID. Entering Forklift
generates a fresh ID; leaving it requires an explicitly supplied real plate.
The `PRIVATE-` prefix is reserved.

`body_type` and `engine_volume` are rejected write fields. Filter by exact
`vehicle_type`. Transmission now permits only `manual` and `automatic`.
VIN, year, fuel, transmission and mileage may be null as applicable. Technical
fields follow the matrix in `app/vehicle_rules.py`. Engine displacement is in
integer cc, masses in kg, dimensions in m, capacities in L. Measurements and
money use Decimal and serialize as exact JSON strings. Counts use integers.
`gas` remains the LPG fuel code for vans/cars; forklift power source uses `lpg`.

Creation defaults apply only to omitted fields. Explicit null stays null.
PATCH omission keeps values; explicit null clears applicable optional fields.
Maintenance records on mileage-tracked vehicles (tractor unit, van, passenger car) may carry a
mileage. An equal or higher value is an ordinary reading. A lower value is refused (web: HTTP 409
with a confirmation popup; API: 409) until the request repeats with `overwrite_mileage=true`
(web: the popup's confirm button). The confirmed value is stored as a correction reading
(`is_correction`): it becomes the vehicle's current mileage and the baseline for later readings,
while all earlier readings remain in the history. Revision `c5e1a7b90d34` adds the flag and a
`seq` insertion order; its downgrade refuses while corrections exist.

Changing type clears incompatible stored values and invalid inherited fuel
choices. Explicit non-null incompatible values are rejected. New readings use
`current_mileage`; logged readings cannot be cleared via explicit API null.
Blank browser Edit readings preserve history. Equal readings add no duplicate
log. Unsupported types reject new readings and retain historical logs. Returning
to a tracked type restores the historical maximum.

## Deployment

1. Back up the database and test restoration in an isolated database.
2. Capture existing department names using the read-only command below; configure
   the approved list. Build/test against disposable PostgreSQL.
3. Stop app, worker and beat while keeping database and storage running.
4. Review the cleanup snapshot, then execute the explicitly confirmed cleanup.
5. Run readiness again and Alembic once; restart all services on the new release.
6. Verify BG/EN workflows, reports, exports and logs. Seed only if requested.

Run from the project root with the intended database configuration:

```bash
python -m scripts.vehicle_demo_cleanup
python -m scripts.vehicle_demo_cleanup --snapshot /safe/path/readiness.json
# Destructive: delete the authorized vehicle demo dataset only, with a NEW snapshot path.
python -m scripts.vehicle_demo_cleanup --confirm-delete-vehicle-demo --snapshot /safe/path/deleted-vehicles.json
python -m scripts.vehicle_demo_cleanup
alembic upgrade head
alembic check
```

Cleanup captures entity IDs and department names, deletes vehicle-dependent
records (contracts before revision `8a3c51d0e7f2`, compliance documents after it) and their document metadata/notifications, and preserves users, drivers,
driver metadata, audit history and object storage. A snapshot path cannot be
overwritten. Cleanup is transactional and idempotent on an empty dataset.

Revision `68ee2a794c32` locks the vehicle table and refuses any remaining vehicle
rows. No legacy classification or liters conversion is performed. Downgrade
also refuses nonempty vehicle tables and never deletes new records. It cannot
restore discarded data; restoring requires the backup and matching old image.
The local demo deployment was cleaned and migrated on 2026-10-21; see the deployment record below.

## Local Compose configuration

Compose passes `VEHICLE_DEPARTMENTS` to app, worker and beat. Set the JSON array in
the local ignored `.env` file; the deployment preserves the existing `trucking`
choice. No department names are built into application defaults.

## Demo deployment record — 2026-10-21

The user authorized discarding vehicle demo records. The full database was backed
up to `/private/tmp/fleetcore-before-six-vehicle-cleanup.dump` and restored into
`fleetcore_vehicle_restore_test_20261021` to verify it. Cleanup removed the old
vehicle and its related records, preserving the existing user and driver. The
entity/departments snapshot is `/private/tmp/fleet-vehicle-rollout/deleted-vehicles.json`.
Revision `68ee2a794c32` was applied; Alembic check found no model drift.
