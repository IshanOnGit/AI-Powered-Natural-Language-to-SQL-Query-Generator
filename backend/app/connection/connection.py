from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import DBAPIError

from app.connection.errors import (
    AuthFailed,
    DatabaseNotFound,
    DBConnectionError,
    InvalidInput,
    Unknown,
    UnsupportedDatabase,
    Unreachable,
)
from app.connection.secret import SecretStr

CONNECT_TIMEOUT_SECONDS = 10
DEFAULT_PORTS = {"postgresql": 5432, "mysql": 3306}
DRIVERS = {"postgresql": "postgresql+psycopg2", "mysql": "mysql+pymysql"}
NICE_NAMES = {"postgresql": "PostgreSQL", "mysql": "MySQL", "sqlite": "SQLite"}


@dataclass
class ConnectionInput:
    db_type: str                       # "postgresql" | "mysql" | "sqlite"
    host: str | None = None            # not used for SQLite
    port: int | None = None            # default: 5432 (Postgres), 3306 (MySQL)
    database: str | None = None        # database name, or file path for SQLite
    username: str | None = None
    password: SecretStr | None = None  # a plain str is wrapped for you

    def __post_init__(self):
        if isinstance(self.password, str):
            self.password = SecretStr(self.password)


class Connection:
    """An open, read-only database connection."""

    def __init__(self, db_type: str, engine: Engine):
        self.db_type = db_type
        self.engine: Engine | None = engine

    @property
    def closed(self) -> bool:
        return self.engine is None

    def close(self) -> None:
        """Close and forget the engine (and so the password). Safe to call twice."""
        engine, self.engine = self.engine, None
        if engine is not None:
            engine.dispose()

    def __repr__(self) -> str:
        return f"Connection(db_type={self.db_type!r}, closed={self.closed})"


def connect(input: ConnectionInput) -> Connection:
    """Open a read-only connection, or raise a DBConnectionError."""
    _check_input(input)
    engine = _make_engine(input)
    try:
        _test(engine, input.db_type)
    except DBConnectionError:
        engine.dispose()
        raise
    except Exception as exc:
        engine.dispose()
        # Fixed messages only: the driver's text can contain the password.
        raise _translate(exc, input) from None
    return Connection(input.db_type, engine)


# --- checking the input ---

def _check_input(input: ConnectionInput) -> None:
    db = (input.db_type or "").strip().lower() if isinstance(input.db_type, str) else ""
    if db not in NICE_NAMES:
        raise UnsupportedDatabase()
    input.db_type = db
    name = NICE_NAMES[db]

    if db == "sqlite":
        if not (input.database or "").strip():
            raise InvalidInput("Database file path is required for SQLite.")
        return

    for value, label in ((input.host, "Host"), (input.database, "Database name"),
                         (input.username, "Username")):
        if not (value or "").strip():
            raise InvalidInput(f"{label} is required for {name}.")
    if input.port is not None and not (isinstance(input.port, int) and 1 <= input.port <= 65535):
        raise InvalidInput("Port must be a number between 1 and 65535.")


# --- building the engine ---

def _build_url(input: ConnectionInput) -> URL:
    """Built with URL.create, never by joining strings, so special characters
    in a password (@ / #) can't break the URL."""
    if input.db_type == "sqlite":
        # file: URI + mode=ro makes SQLite refuse every write (the second lock).
        uri = Path(input.database.strip()).resolve().as_uri()
        return URL.create("sqlite", database=uri, query={"mode": "ro", "uri": "true"})

    return URL.create(
        DRIVERS[input.db_type],
        username=input.username.strip(),
        password=input.password.get_secret_value() if input.password else None,
        host=input.host.strip(),
        port=input.port or DEFAULT_PORTS[input.db_type],
        database=input.database.strip(),
    )


def _connect_args(db_type: str) -> dict:
    if db_type == "postgresql":
        # every transaction on this connection is read-only
        return {
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "options": "-c default_transaction_read_only=on",
        }
    if db_type == "mysql":
        return {
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "init_command": "SET SESSION TRANSACTION READ ONLY",
        }
    return {}


def _make_engine(input: ConnectionInput) -> Engine:
    if input.db_type == "sqlite" and not Path(input.database.strip()).is_file():
        raise DatabaseNotFound(f"Database file '{input.database.strip()}' was not found.")
    return create_engine(
        _build_url(input),
        connect_args=_connect_args(input.db_type),
        pool_size=1,          # minimal pool: one connection, no extra
        max_overflow=0,
        pool_timeout=CONNECT_TIMEOUT_SECONDS,
        pool_pre_ping=True,
    )


def _test(engine: Engine, db_type: str) -> None:
    """Prove the connection really works."""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        if db_type == "sqlite":
            # SELECT 1 never opens the file, so a non-database file would pass.
            conn.execute(text("SELECT count(*) FROM sqlite_master"))


# --- turning driver errors into our simple ones ---

def _translate(exc: Exception, input: ConnectionInput) -> DBConnectionError:
    if not isinstance(exc, DBAPIError):
        return Unknown()
    orig = exc.orig
    text_ = str(orig).lower()
    code = orig.args[0] if getattr(orig, "args", None) and isinstance(orig.args[0], int) else None
    pgcode = getattr(orig, "pgcode", None)

    if input.db_type == "sqlite":
        # (a missing file was already handled; this is a damaged or wrong file)
        if "not a database" in text_ or "malformed" in text_:
            return InvalidInput("That file is not a valid SQLite database.")
        return Unknown()

    if code == 1045 or pgcode in ("28P01", "28000") or any(
        w in text_ for w in ("password authentication failed", "access denied", "no pg_hba.conf")
    ):
        return AuthFailed()
    name = (input.database or "").strip()
    if code == 1049 or pgcode == "3D000" or (
        "database" in text_ and ("does not exist" in text_ or "unknown database" in text_)
    ):
        return DatabaseNotFound(f"Database '{name}' was not found.")
    if code in (2002, 2003, 2005, 2006, 2013) or any(
        w in text_ for w in (
            "connection refused", "could not connect", "could not translate host",
            "timeout expired", "timed out", "name or service not known",
            "can't connect", "no route to host", "network is unreachable",
            "temporary failure in name resolution",
        )
    ):
        return Unreachable()
    return Unknown()
