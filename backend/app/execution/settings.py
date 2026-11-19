import os

from app.errors import AppError


def load_timeout_seconds() -> float:
    """QUERY_TIMEOUT_SECONDS, default 15. Read each time (so tests can change it)."""
    raw = os.environ.get("QUERY_TIMEOUT_SECONDS", "15")
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not value > 0 or value == float("inf"):
        raise AppError("query_setup_problem", "Setting QUERY_TIMEOUT_SECONDS must be a number above 0.")
    return value
