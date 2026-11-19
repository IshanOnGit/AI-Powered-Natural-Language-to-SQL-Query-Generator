import re
import threading
import time

from sqlalchemy.exc import DBAPIError

from app.connection import Connection
from app.execution.errors import (
    ConnectionLost,
    ExecutionError,
    QueryFailed,
    Timeout,
    Unknown,
)
from app.execution.models import ExecutionResult
from app.execution.settings import load_timeout_seconds
from app.execution.values import to_display
from app.validation import ValidatedQuery

MAX_ERROR_TEXT = 300

# Words in a driver error that say "we ran out of time" / "the link broke".
TIMEOUT_WORDS = ("statement timeout", "maximum statement execution time", "execution time exceeded")
LOST_WORDS = (
    "server closed the connection", "connection already closed", "connection has been closed",
    "lost connection", "server has gone away", "terminating connection",
    "connection reset", "broken pipe", "could not receive data", "connection is closed",
    "ssl syscall error", "connection not open",
)
TIMEOUT_CODES = {3024}              # MySQL: max_execution_time exceeded
LOST_CODES = {2006, 2013, 2055}     # MySQL: gone away / lost connection


def execute(connection: Connection, query: ValidatedQuery) -> ExecutionResult:
    """Run an approved query with a time limit. Raises an ExecutionError."""
    if not isinstance(query, ValidatedQuery):
        # A plain string would skip validation. This is a bug in the caller.
        raise TypeError("execute() accepts only a ValidatedQuery.")
    if connection.engine is None:
        raise ConnectionLost()

    timeout = load_timeout_seconds()
    timer = None
    timed_out = threading.Event()
    started = time.perf_counter()
    try:
        with connection.engine.connect() as conn:    # leaving the block rolls back: nothing is saved
            # Send the SQL exactly as validated: no bind-parameter or % handling.
            conn = conn.execution_options(no_parameters=True)
            if connection.db_type == "postgresql":
                conn.exec_driver_sql(f"SET statement_timeout = {int(timeout * 1000)}")
            elif connection.db_type == "mysql":
                conn.exec_driver_sql(f"SET SESSION max_execution_time = {int(timeout * 1000)}")
            else:
                timer = _start_sqlite_timer(conn, timeout, timed_out)

            result = conn.exec_driver_sql(query.sql)
            columns = [str(c) for c in result.keys()]
            rows = [[to_display(v) for v in row] for row in result.fetchall()]
    except ExecutionError:
        raise
    except Exception as exc:
        raise _translate(exc, connection, timed_out.is_set()) from None
    finally:
        if timer is not None:
            timer.cancel()

    return ExecutionResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


def _start_sqlite_timer(conn, timeout: float, timed_out: threading.Event) -> threading.Timer:
    """SQLite has no timeout of its own: stop the query from a timer."""
    raw = conn.connection.driver_connection      # the sqlite3 connection

    def stop():
        timed_out.set()
        raw.interrupt()

    timer = threading.Timer(timeout, stop)
    timer.daemon = True
    timer.start()
    return timer


# --- turning driver errors into our simple ones ---

def _translate(exc: Exception, connection: Connection, sqlite_timed_out: bool) -> ExecutionError:
    if not isinstance(exc, DBAPIError):
        return Unknown()

    orig = exc.orig
    text = _short_text(orig, connection)
    lower = text.lower()
    code = orig.args[0] if getattr(orig, "args", None) and isinstance(orig.args[0], int) else None
    pgcode = getattr(orig, "pgcode", None)

    if sqlite_timed_out or pgcode == "57014" or code in TIMEOUT_CODES or any(w in lower for w in TIMEOUT_WORDS):
        return Timeout()
    if exc.connection_invalidated or code in LOST_CODES or any(w in lower for w in LOST_WORDS):
        return ConnectionLost()
    return QueryFailed(f"The database couldn't run this query: {text}")


def _short_text(orig, connection: Connection) -> str:
    """The database's own short message, tidied, trimmed, password scrubbed."""
    diag = getattr(orig, "diag", None)
    text = getattr(diag, "message_primary", None)          # PostgreSQL: just the main message
    if not text:
        args = getattr(orig, "args", ())
        text = str(args[1]) if len(args) > 1 and isinstance(args[0], int) else str(orig)  # MySQL: (code, text)
    text = " ".join(text.split())
    password = connection.engine.url.password if connection.engine is not None else None
    if password:
        text = text.replace(password, "***")
    text = re.sub(r"://[^\s/@:]*:[^\s/@]*@", "://***@", text)   # any user:password@ in the text
    return text[:MAX_ERROR_TEXT]
