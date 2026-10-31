"""Read the Shell Card "Priced Transaction" CSV export (Toll4Europe road tax) into plain rows.

Only the parsing lives here: which columns we need, how text, dates and amounts are written in the file. Matching
rows to vehicles and deciding what to import is ``TollImportService``'s job.

The portal writes UTF-8 with a BOM and ``;`` separators; we also accept UTF-16/32 and ``,`` / tab, because a file
re-saved by Excel or another export changes both. Excel also prefixes long numbers with ``'`` to keep them as text.
"""

import csv
import datetime as dt
import io
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from app.services.errors import DomainError

SOURCE = "shell_priced"
MAX_ROWS = 20000

# field -> accepted header names (the portal export in Bulgarian)
COLUMNS = {
    "transaction_id": ("Идентификатор на трансакция",),
    "plate": ("Регистрационен номер на превозното средство",),
    "date": ("Дата на доставка",),
    "time": ("Време",),
    "amount": ("Брутна покупна цена във валута на трансакцията (вкл ДДС и след отстъпка)",),
    "currency": ("Валута на трансакцията",),
    "product": ("Име на продукт",),
    "network": ("Име на мрежата",),
    "invoice_number": ("Номер на фактурата",),
    "refund": ("Флаг за възстановяване",),
}
REQUIRED = ("transaction_id", "plate", "date", "amount", "currency")
# Only in Shell's other report ("Toll Transaction"): recognised so the user is told which file to upload instead.
TOLL_TRANSACTION_MARKER = "Код на оператора за пътни такси"

DATE_FORMATS = ("%d/%m/%y", "%d/%m/%Y", "%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d")
TIME_FORMATS = ("%I:%M:%S %p", "%I:%M %p", "%H:%M:%S", "%H:%M")


@dataclass
class TollRow:
    """One file line. ``problem`` is set when the line itself is unusable (it is then never imported)."""

    line: int
    transaction_id: str | None
    plate: str | None
    date: dt.date | None
    time: dt.time | None
    amount: Decimal | None
    currency: str | None
    product: str | None
    network: str | None
    invoice_number: str | None
    problem: str | None = None
    problem_field: str | None = None
    raw: dict = field(default_factory=dict)


def decode(data: bytes) -> str:
    """Text of the file; the byte-order mark decides the encoding, otherwise UTF-8 (then Windows-1251)."""
    if data.startswith((b"PK\x03\x04", b"\xd0\xcf\x11\xe0")):  # .xlsx / .xls
        raise DomainError("error.toll_import_excel")
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        encodings = ("utf-32",)
    elif data.startswith((b"\xff\xfe", b"\xfe\xff")):
        encodings = ("utf-16",)
    else:
        encodings = ("utf-8-sig", "cp1251")
    for encoding in encodings:
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise DomainError("error.toll_import_encoding")


def _clean(value: str | None) -> str | None:
    """Trim, drop Excel's leading ``'`` text marker; empty becomes None."""
    if value is None:
        return None
    value = value.strip()
    if value.startswith("'"):
        value = value[1:].strip()
    return value or None


def parse_date(value: str | None) -> dt.date | None:
    for fmt in DATE_FORMATS:
        try:
            return dt.datetime.strptime(value or "", fmt).date()
        except ValueError:
            continue
    return None


def parse_time(value: str | None) -> dt.time | None:
    for fmt in TIME_FORMATS:
        try:
            return dt.datetime.strptime(value or "", fmt).time()
        except ValueError:
            continue
    return None


def parse_amount(value: str | None) -> Decimal | None:
    """Exact amount in cents; None when it is not a number or has non-zero digits below a cent."""
    if value is None:
        return None
    text = value.replace(" ", "")
    if "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount != amount.quantize(Decimal("0.01")):
        return None
    return amount.quantize(Decimal("0.01"))


def _delimiter(header_line: str) -> str:
    return max((";", ",", "\t"), key=header_line.count)


def parse(data: bytes) -> list[TollRow]:
    """All data lines of a Priced Transaction export, in file order.

    Raises ``DomainError`` when the file as a whole is not that export (wrong report, missing columns, too long).
    """
    text = decode(data)
    first_line = text.split("\n", 1)[0]
    if TOLL_TRANSACTION_MARKER in first_line:
        raise DomainError("error.toll_import_wrong_report")
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_delimiter(first_line))
    try:
        header = [(_clean(h) or "") for h in next(reader)]
    except StopIteration:
        raise DomainError("error.toll_import_empty") from None
    index = {}
    for name, aliases in COLUMNS.items():
        position = next((header.index(a) for a in aliases if a in header), None)
        if position is not None:
            index[name] = position
    missing = [COLUMNS[name][0] for name in REQUIRED if name not in index]
    if missing:
        raise DomainError("error.toll_import_columns", columns=", ".join(missing))

    rows: list[TollRow] = []
    for line_no, values in enumerate(reader, start=2):
        if not any(v.strip() for v in values):
            continue
        if len(rows) >= MAX_ROWS:
            raise DomainError("error.toll_import_too_many_rows", limit=MAX_ROWS)
        raw = {name: _clean(values[pos]) if pos < len(values) else None for name, pos in index.items()}
        row = TollRow(
            line=line_no,
            transaction_id=raw.get("transaction_id"),
            plate=raw.get("plate"),
            date=parse_date(raw.get("date")),
            time=parse_time(raw.get("time")),
            amount=parse_amount(raw.get("amount")),
            currency=(raw.get("currency") or "").upper() or None,
            product=raw.get("product"),
            network=raw.get("network"),
            invoice_number=raw.get("invoice_number"),
            raw=raw,
        )
        _check(row)
        rows.append(row)
    return rows


def _check(row: TollRow) -> None:
    """Mark the first thing that makes a line unusable on its own (before any vehicle lookup)."""
    for name in ("transaction_id", "plate", "date", "amount", "currency"):
        if not row.raw.get(name):
            row.problem, row.problem_field = "missing_field", name
            return
    if row.date is None:
        row.problem, row.problem_field = "bad_date", "date"
    elif row.amount is None:
        row.problem, row.problem_field = "bad_amount", "amount"
    elif row.currency != "EUR":
        row.problem, row.problem_field = "not_eur", "currency"
    elif (row.raw.get("refund") or "").upper() == "Y" or row.amount < 0:
        row.problem = "refund"
