# Toll import (Shell Card)

Expenses → **Import tolls** (`/expenses/imports`, admin and fleet_manager only) turns the Shell Card
portal's **Priced Transaction** CSV into toll expenses.

## Flow

1. Upload the CSV. Nothing is saved yet: a preview shows the period, new transactions with their total,
   transactions that are already imported, and the lines that cannot be imported.
2. **Import** checks again against the current data and writes everything in one transaction.
   **Cancel** discards the preview.

## Rules

- One toll expense per vehicle and day. New transactions for a day that already has an *imported*
  expense make it larger; an expense someone typed in by hand is never merged.
- Each transaction is stored in `toll_transactions` with Shell's transaction ID, which is unique per
  source. Uploading overlapping periods again is safe:
  - same ID, vehicle, date and amount → skipped (a newly issued invoice number is copied onto it);
  - same ID with a different vehicle, date or amount → reported as "changed", never overwritten.
- Plates match regardless of spaces, dashes or Cyrillic look-alike letters (`app/utils/plates.py`).
- A line that can't be imported blocks only itself: unknown or ambiguous plate, trailer, decommissioned
  vehicle, missing value, unreadable date or amount, non-EUR currency, refund or negative amount. These
  lines stay on their upload, and the import page lists them as waiting until a later upload imports
  them. The uploader gets an in-app message naming the missing plates.
- Deleting an imported expense deletes its transactions too, so the next upload adds them again.
- Every upload is audited (`expense_import`); each expense it creates is audited as usual.

## File format

`app/utils/shell_toll_csv.py`: columns are matched by their Bulgarian header names. The importer
accepts UTF-8, UTF-16 and UTF-32 files, `;`, `,` or tab separators, and Excel's leading `'` on
long numbers. The amount comes from the gross column ("вкл ДДС и след отстъпка"), in EUR only.
Shell's separate "Toll Transaction" report and Excel files are rejected with a message saying
which file to upload instead.

Not covered yet: card fees, service fees and monetary corrections from the invoice; refunds.
