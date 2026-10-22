# Planned service items and next due (Планово обслужване)

A planned service (`type = scheduled_service`) can name **what was serviced** and when it is due
again, so each oil change is tracked on its own.

## Fields

- `service_item` (nullable string): `engine_oil`, `gearbox_oil`, `differential_oil`, `brake_fluid`,
  `coolant`. Defined only in `app/maintenance_rules.py` (`ServiceItem`, `SERVICE_ITEMS`); adding one needs
  labels `service_item.<code>` in both catalogs and no migration. Empty = "Other / not specified" (not tracked).
- `service_checklist` (nullable JSON list): extra work ticked off during the service. Oil filter, air
  filter and fuel filter are **checklist options, not service items**: they appear as checkboxes when
  "Engine oil" is selected (`SERVICE_CHECKLIST` in the rules maps an item to its options; labels
  `service_check.<code>`). They are recorded on the service and shown on its detail page; they are not
  tracked or warned about separately.
- `next_service_mileage` ("Next service at (km)") and `next_service_date`: entered by the user
  (for example mileage + 60,000 km). Both optional.

The form shows these three fields only for the type "Планово обслужване". Rules:

- **Trailers** (container and curtain-side semi-trailers) have no service items: the dropdown is hidden
  for them and the server rejects an item for a trailer (`service_items_for` in the rules). The next km /
  date fields stay available but are not tracked or warned about without an item.
- A checklist only fits the item that offers it (engine oil); changing the item drops checks it does not offer.
- A service item is only allowed on planned services. Changing the type of a record to something else
  clears its item (the next km / date are kept).
- The next service km must be higher than the km of the service itself when both are set.

## Status (computed, never stored)

Per vehicle and service item the **latest completed** planned service is current (by completed date);
older ones are history and never warn. A newer service without a next km / date replaces the older due.

- `overdue`: due date before today, or the vehicle's current mileage reached the due km
- `due_soon`: due date within `SERVICE_DUE_SOON_DAYS` (30) or due km within `SERVICE_DUE_SOON_KM` (10,000)
- `ok`, and `none` (the current service has neither next km nor date)

Vehicles without an odometer (trailers, forklifts) are judged by date only. Decommissioned vehicles
never warn. A due date of today is "due soon" (the last day).

## Where it shows

- Maintenance form, detail page (with the state of the current service) and list (item under the title)
- Vehicle page, *Maintenance* tab: table "Service due" above the history
- `/maintenance/due` (button on the maintenance list): all current services, most urgent first,
  filter by status. The dashboard "attention" widget links to it for overdue and due-soon items
- `GET /api/v1/maintenance/service-due?status=&vehicle_id=`

## Reminders

Like vehicle and driver documents, warnings are computed: the dashboard and the pages above show them and
the daily Celery task `check_service_due` (08:00 UTC) counts overdue / due-soon items per service item.
Sending email or Telegram notifications is left for the reminders feature. Restart the worker and beat
after deployment so the new task is scheduled.

## Deployment

Revision `f4c8d2e91a57` adds the nullable column `maintenance_records.service_item`. Existing records keep
no item and are not tracked until you set one. Downgrade drops the column (and the items).

Revision `a1d7e5b3c902` adds `maintenance_records.service_checklist` (JSONB) and converts any record whose item
was `oil_filter`, `air_filter` or `fuel_filter` into an `engine_oil` service with that filter ticked.
Downgrade refuses while checklists exist, unless run as `alembic -x drop_service_checklist=yes downgrade -1`
(the ticked filters are dropped, not turned back into items).

## Maintenance and expense lists

Both lists filter live (HTMX) by vehicle like the vehicle list: search by plate, VIN, brand or model, and by
vehicle type (`trailer` = both trailer kinds). The maintenance list also filters by status (including
**Pending** = scheduled + in progress) and by **when**: *Overdue* (scheduled, date passed) or *Upcoming*
(scheduled within `MAINTENANCE_UPCOMING_DAYS`, default 14). Pending lists sort the soonest first. The dashboard rows
"overdue maintenance" and "upcoming maintenance" open `/maintenance?status=scheduled&when=overdue|upcoming`, so the
list shows exactly the counted records; the status boxes of the maintenance widget link to the status filters.
The API accepts the same filters (`q`, `vehicle_type`, `status=pending`, `when`).
