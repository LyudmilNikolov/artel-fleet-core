"""Compare licence plates the way a person reads them.

Bulgarian plates use only the Cyrillic letters that look like Latin ones (А В Е К М Н О Р С Т У Х), and people
type them either way, with or without spaces. ``CO 7930 CX`` (Latin), ``СО7930СХ`` (Cyrillic) and ``co-7930-cx``
all mean the same vehicle.
"""

CYRILLIC_TO_LATIN = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")
_LOOKALIKES = "АВЕКМНОРСТУХ" + "авекмнорстух"
_LATIN_TWINS = "ABEKMHOPCTYX" + "abekmhopctyx"


def normalize_plate(plate: str) -> str:
    """Stored form of a plate: upper case, with Cyrillic look-alike letters replaced by their Latin twins.

    Spacing and every other character are kept. Applied on save and before every duplicate lookup.
    """
    return plate.upper().translate(CYRILLIC_TO_LATIN)


def plate_sql_key(column):
    """SQL expression equal to ``normalize_plate`` of a stored plate, so rows saved before it existed still match."""
    from sqlalchemy import func

    return func.upper(func.translate(column, _LOOKALIKES, _LATIN_TWINS))


def plate_key(plate: str | None) -> str:
    """Upper-case Latin letters and digits only; '' for no plate."""
    text = (plate or "").upper().translate(CYRILLIC_TO_LATIN)
    return "".join(ch for ch in text if ch.isalnum())
