# Company licence (Лиценз на фирмата)

The company's international road haulage licence (the original, issued by the Ministry of Transport; for
example № 22098 valid 14.05.2020 to 13.05.2030). Each tractor unit carries a certified copy with its own number,
which is tracked per truck as the *Transport licence (copy)* document type, see
[compliance-documents.md](compliance-documents.md).

Page: **Company licence** in the sidebar (`/company-licence`). Admin and fleet_manager add, edit, renew and delete;
viewers read; drivers do not see it.

## Data

Table `company_licences`: `licence_number` (required), `holder` (required, company name and address), `start_date`,
`end_date` (inclusive, `end >= start`, both required), `issued_by`, `notes`, `renewed_from_id` (set null on delete),
`created_by`, timestamps. The scan of the original uses the documents feature with `entity_type = company_licence`
(upload and delete: admin and fleet_manager).

## Rules

- Status (valid / expiring soon / expired) is computed from `end_date` with `COMPLIANCE_EXPIRING_SOON_DAYS`, never stored.
- The entry with the latest `end_date` is current; older entries are history and never warn.
- Renew creates a new entry (number, holder and issuer prefilled, next period proposed); the old entry stays as history.
- Deleting an entry deletes its scans and files; the previous entry becomes current again.
- When the current entry is expiring soon or expired, the dashboard shows a banner (admin, fleet_manager, viewer).
- Create, update and delete are audited (`company_licence`).

## Not covered yet

- Email / Telegram / bell reminders for the company licence. The reminder rules engine works on vehicle and driver
  documents (`app/notification_rules.py`, `attention_service.py`); the company licence only shows the dashboard banner.
- A REST API for the company licence.
- Sending a licence pack (licence, insurance, Голям талон) in one step; the Голям талон is not tracked.

## Deployment

Revision `b3f7c1d9a2e5` is additive: it adds `company_licences` and the `COMPANY_LICENCE` label of the `entity_type`
enum. Downgrade refuses while the table or its scans hold data and leaves the unused enum label in place. The new
vehicle document type needs no migration. Take the usual manual backup before the release.
