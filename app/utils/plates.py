"""Compare licence plates the way a person reads them.

Bulgarian plates use only the Cyrillic letters that look like Latin ones (А В Е К М Н О Р С Т У Х), and people
type them either way, with or without spaces. ``CO 7930 CX`` (Latin), ``СО7930СХ`` (Cyrillic) and ``co-7930-cx``
all mean the same vehicle.
"""

CYRILLIC_TO_LATIN = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")


def plate_key(plate: str | None) -> str:
    """Upper-case Latin letters and digits only; '' for no plate."""
    text = (plate or "").upper().translate(CYRILLIC_TO_LATIN)
    return "".join(ch for ch in text if ch.isalnum())
