import os
from dataclasses import dataclass

from app.errors import AppError


@dataclass(frozen=True)
class QuerySettings:
    default_limit: int    # LIMIT added when the query has none
    max_limit: int        # highest LIMIT allowed


def _whole_number(name: str, default: str) -> int:
    raw = os.environ.get(name, default)
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise AppError("query_setup_problem", f"Setting {name} must be a whole number above 0.")
    return value


def load_settings() -> QuerySettings:
    """Read from the environment each time (so tests can change them)."""
    default_limit = _whole_number("QUERY_DEFAULT_LIMIT", "100")
    max_limit = _whole_number("QUERY_MAX_LIMIT", "1000")
    if default_limit > max_limit:
        raise AppError("query_setup_problem", "QUERY_DEFAULT_LIMIT can't be bigger than QUERY_MAX_LIMIT.")
    return QuerySettings(default_limit, max_limit)
