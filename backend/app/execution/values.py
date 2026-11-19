import math
from datetime import date, datetime, time
from decimal import Decimal

MAX_TEXT_LENGTH = 1000


def to_display(value):
    """Make a database value safe to send as JSON and show in a table.

    Result is always str, int, float, bool or None.
    """
    if value is None:
        return None
    if isinstance(value, bool):               # before int: True is an int in Python
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        # JSON can't carry NaN or infinity
        return value if math.isfinite(value) else _shorten(str(value))
    if isinstance(value, Decimal):
        return _shorten(format(value, "f"))   # exact digits: "12.50", never "1.25E+1"
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<binary, {len(bytes(value))} bytes>"
    return _shorten(str(value))               # text, arrays, JSON, UUID, ...


def _shorten(text: str) -> str:
    """One huge value must not make the response heavy. Display only."""
    if len(text) > MAX_TEXT_LENGTH:
        return text[:MAX_TEXT_LENGTH] + "…"
    return text
