# Reports

Four reports share one filter bar and one filter object: **TCO**, **Fuel consumption**, **Expense analysis**
and **Maintenance history** (`/reports/tco|fuel|expenses|maintenance`, API under `/api/v1/reports/`).

## Filters

| Parameter | Meaning |
|---|---|
| `vehicle_id` | One vehicle (searchable picker; forklifts show `Brand Model · PRIVATE-XXXXXXXX`). |
| `vehicle_type` | Group: `tractor_unit`, `trailer` (both trailer kinds), `van`, `passenger_car`, `forklift`; empty = all. Mapping lives in `app/vehicle_rules.py` (`vehicle_type_filter`, `type_group_of`). |
| `start_date`, `end_date` | Inclusive, `YYYY-MM-DD`. |
| `status` | Maintenance only. |
| `category` | Expense analysis only: one expense category (`fuel`, `toll`, ...); empty = all. Applies to the page, the CSV export and `/api/v1/reports/expense-analysis`. |

- Vehicle and type work together (AND). The picker narrows to the chosen type and a mismatching vehicle is cleared;
  a hand-edited URL with a vehicle outside the type returns an empty report with a note.
- Parsing is `parse_report_filters` (`app/schemas/report.py`) for web and API. Bad input gives a localized 422
  (`error.report_*`), never a 500.
- Export links carry the same parameters, so an export always equals the screen.
- Queries live in `app/repositories/report_repo.py`; there is no per-vehicle querying.

## Rules

- **TCO**: the date range limits expenses only. Vehicles without expenses in the period stay in the report;
  purchase price is not date-filtered.
- **Fuel**: L/100 km is full-to-full. Per mileage-tracked vehicle (tractor unit, van, passenger car) and only
  refuels with `mileage_at_refuel`: liters of all but the first refuel over (highest - lowest mileage). It needs
  two such refuels and a positive distance, otherwise it shows "-". Trailers have no fuel expenses (empty state);
  forklifts show liters and cost but no L/100 km. A mileage correction can make the distance too small.
- **Maintenance**: the date filter applies to the *service date* = completed date, else scheduled date. The total
  cost excludes cancelled records; null cost counts as zero. The page is paginated (50, max 200); the API list
  and the Excel export contain all matching records.

## Access

Web report pages and web exports: any logged-in user. API data endpoints: any authenticated user. API exports:
admin and fleet manager.

## Tests

Same disposable PostgreSQL setup as `driver-documents.md`. In zsh, pass the `-e` flags inline (an unquoted
variable is not word-split).
