"""Compare a generated result with the reference result (spec section 5)."""
import math

TOLERANCE = 1e-6


def _number(value):
    """A real number, or text that is a number ('12.50'), else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            number = float(value)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return None


def values_match(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, str) and isinstance(b, str):
        if a == b:
            return True            # text is compared exactly...
    x, y = _number(a), _number(b)
    if x is not None and y is not None:   # ...but numbers (and numeric text) get a tiny tolerance
        return abs(x - y) <= TOLERANCE
    return a == b


def rows_match(row_a, row_b) -> bool:
    return len(row_a) == len(row_b) and all(values_match(a, b) for a, b in zip(row_a, row_b))


def results_match(expected_rows, actual_rows, ordered: bool = False) -> bool:
    """Columns are compared by position, never by name. Row order only matters
    when `ordered` is True. An extra or missing column makes it wrong."""
    if len(expected_rows) != len(actual_rows):
        return False
    if ordered:
        return all(rows_match(e, a) for e, a in zip(expected_rows, actual_rows))
    unused = list(actual_rows)
    for expected in expected_rows:
        for i, actual in enumerate(unused):
            if rows_match(expected, actual):
                del unused[i]
                break
        else:
            return False
    return True
