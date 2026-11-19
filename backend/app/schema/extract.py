import re
import time
import unicodedata
from typing import Protocol

from sqlalchemy import inspect
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, NoSuchTableError, SQLAlchemyError

from app.schema.errors import (
    ConnectionLost,
    EmptySchema,
    PermissionDenied,
    SchemaError,
    Timeout,
    Unknown,
)
from app.schema.models import Column, ForeignKey, SchemaInfo, Table

MAX_TABLES_TESTED = 20          # intent: V1 is tested up to about 20 tables
TIMEOUT_SECONDS = 15.0          # for the whole extraction
MAX_TYPE_LENGTH = 40
SYSTEM_PREFIXES = ("sqlite_", "pg_")
PERMISSION_WORDS = ("permission denied", "access denied", "command denied")


class _Connection(Protocol):
    """What we need from the Connection component (spec-connection.md)."""

    db_type: str     # "postgresql" | "mysql" | "sqlite"
    engine: Engine


def extract_schema(connection: _Connection, timeout_seconds: float = TIMEOUT_SECONDS) -> SchemaInfo:
    """Read the schema, or raise a SchemaError. Reads structure only, never rows."""
    deadline = time.monotonic() + timeout_seconds
    try:
        return _extract(connection, deadline)
    except SchemaError:
        raise
    except Exception as exc:
        # Fixed messages only: the driver's text can contain the password.
        raise _translate(exc) from None


def _check_time(deadline: float) -> None:
    if time.monotonic() > deadline:
        raise Timeout()


def _extract(connection: _Connection, deadline: float) -> SchemaInfo:
    clean = _Cleaner()
    inspector = inspect(connection.engine)
    # PostgreSQL: public only. MySQL / SQLite: the connected database.
    schema = "public" if connection.db_type == "postgresql" else None

    _check_time(deadline)
    names = [(n, "table") for n in inspector.get_table_names(schema=schema)]
    names += [(n, "view") for n in inspector.get_view_names(schema=schema)]
    names = [(n, kind) for n, kind in names if not _is_system(n)]
    if not names:
        raise EmptySchema()

    tables = []
    for name, kind in names:
        _check_time(deadline)
        try:
            tables.append(_read_table(inspector, name, kind, schema, clean))
        except NoSuchTableError:
            continue  # dropped while we were reading
    if not tables:
        raise EmptySchema()

    tables.sort(key=lambda t: (t.name.lower(), t.name))
    warnings = []
    if len(tables) > MAX_TABLES_TESTED:
        warnings.append(
            f"Schema has {len(tables)} tables; V1 is tested up to ~{MAX_TABLES_TESTED}."
        )
    if clean.changed:
        n = len(clean.changed)
        warnings.append(f"{n} identifier{' was' if n == 1 else 's were'} sanitised.")
    return SchemaInfo(db_type=connection.db_type, tables=tables, warnings=warnings)


def _is_system(name: str) -> bool:
    return name.lower().startswith(SYSTEM_PREFIXES)


def _read_table(inspector, name, kind, schema, clean: "_Cleaner") -> Table:
    pk = set(inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") or [])
    columns = [
        Column(
            name=clean.name(c["name"]),
            type=clean.type(c["type"], inspector.dialect),
            nullable=bool(c.get("nullable", True)),
            primary_key=c["name"] in pk,
        )
        for c in inspector.get_columns(name, schema=schema)
    ]
    columns.sort(key=lambda c: (c.name.lower(), c.name))

    foreign_keys = [
        ForeignKey(
            columns=[clean.name(x) for x in fk["constrained_columns"]],
            ref_table=clean.name(fk["referred_table"]),
            ref_columns=[clean.name(x) for x in (fk.get("referred_columns") or [])],
        )
        for fk in (inspector.get_foreign_keys(name, schema=schema) if kind == "table" else [])
    ]
    foreign_keys.sort(key=lambda f: (f.columns, f.ref_table, f.ref_columns))
    return Table(name=clean.name(name), kind=kind, columns=columns, foreign_keys=foreign_keys)


class _Cleaner:
    """Makes names safe to put in a prompt: no newlines or control characters.

    Remembers which names had to change, so we can add a warning.
    """

    def __init__(self):
        self.changed: set[str] = set()

    def name(self, original: str) -> str:
        cleaned = "".join("_" if _is_control(ch) else ch for ch in str(original))
        if cleaned != original:
            self.changed.add(original)
        return cleaned

    def type(self, sql_type, dialect) -> str:
        """'VARCHAR(120)' -> 'VARCHAR'. Unknown types become ''."""
        try:
            text = sql_type.compile(dialect=dialect)
        except Exception:
            text = ""
        text = re.sub(r"\(.*?\)", "", text)       # drop lengths
        text = " ".join(text.split())             # tidy spaces
        return self.name(text)[:MAX_TYPE_LENGTH]


def _is_control(ch: str) -> bool:
    return unicodedata.category(ch)[0] == "C" or unicodedata.category(ch) in ("Zl", "Zp")


def _translate(exc: Exception) -> SchemaError:
    if isinstance(exc, DBAPIError):
        text = str(exc).lower()
        if any(word in text for word in PERMISSION_WORDS):
            return PermissionDenied()
        if exc.connection_invalidated or type(exc).__name__ in ("OperationalError", "InterfaceError"):
            return ConnectionLost()
    if isinstance(exc, SQLAlchemyError) and "timeout" in type(exc).__name__.lower():
        return Timeout()
    return Unknown()
