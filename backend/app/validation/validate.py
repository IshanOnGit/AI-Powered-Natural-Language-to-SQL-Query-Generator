import logging
import re

import sqlglot
from sqlglot import exp
from sqlglot.dialects.dialect import Dialect
from sqlglot.errors import SqlglotError

from app.validation import models as m
from app.validation.models import ValidatedQuery, ValidationResult
from app.validation.settings import QuerySettings, load_settings

# sqlglot logs a warning for statements it can only read as a raw "Command"
# (we reject those anyway), so keep the console quiet.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

DIALECTS = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite"}

# A denylist can never be complete, so it only names well-known harmful
# functions. The read-only connection and the time limit cover the rest.
FORBIDDEN_FUNCTIONS = {
    "postgresql": {"pg_sleep", "pg_sleep_for", "pg_sleep_until", "pg_read_file",
                   "pg_read_binary_file", "set_config", "dblink", "dblink_exec",
                   "lo_import", "lo_export"},
    "mysql": {"sleep", "benchmark", "load_file"},
    "sqlite": {"load_extension", "readfile", "writefile"},
}

# Anything of these kinds, anywhere in the tree, means "not read-only".
_WRITE_NAMES = (
    "Insert", "Update", "Delete", "Merge", "Create", "Alter", "Drop", "TruncateTable",
    "Command", "Copy", "Set", "Pragma", "Attach", "Detach", "Use", "Grant", "Revoke",
    "Transaction", "Commit", "Rollback", "Show", "Describe", "LoadData", "Analyze",
    "Kill", "Declare", "Execute", "Cache", "Uncache", "Comment",
)
WRITE_NODES = tuple(getattr(exp, n) for n in _WRITE_NAMES if hasattr(exp, n))

MESSAGES = {
    m.EMPTY: "No query to check.",
    m.SYNTAX_ERROR: "The query isn't valid SQL for this database.",
    m.MULTIPLE_STATEMENTS: "Only one query at a time is allowed.",
    m.NOT_READ_ONLY: "Only read-only SELECT queries are allowed.",
    m.FORBIDDEN_CLAUSE: "This query uses a clause that isn't allowed.",
    m.FORBIDDEN_FUNCTION: "This query uses a function that isn't allowed.",
}


def validate(sql: str, db_type: str) -> ValidationResult:
    """Decide: safe to show and run, or rejected. A rejection is a normal
    result, not an exception."""
    if db_type not in DIALECTS:
        raise ValueError("db_type must be 'postgresql', 'mysql' or 'sqlite'.")
    dialect = DIALECTS[db_type]
    settings = load_settings()

    # 1. Not empty
    if not isinstance(sql, str) or not sql.strip():
        return _reject(m.EMPTY)

    # 2. Must parse (in the connected database's dialect)
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except (SqlglotError, RecursionError):
        # MySQL's INTO OUTFILE doesn't parse. Give it its proper reason.
        if _has_file_output(sql, dialect):
            return _reject(m.FORBIDDEN_CLAUSE, "INTO OUTFILE")
        return _reject(m.SYNTAX_ERROR)

    # 3. Exactly one statement (a trailing ";" leaves an empty slot: ignored)
    statements = [s for s in statements if s is not None]
    if not statements:
        return _reject(m.EMPTY)  # only a comment
    if len(statements) > 1:
        return _reject(m.MULTIPLE_STATEMENTS)
    stmt = statements[0]

    # 4. Read-only query, and no write hiding anywhere inside it
    if not _is_query(stmt) or any(isinstance(node, WRITE_NODES) for node in stmt.walk()):
        return _reject(m.NOT_READ_ONLY)

    # 5. No forbidden clauses
    for node in stmt.walk():
        if isinstance(node, exp.Into):
            return _reject(m.FORBIDDEN_CLAUSE, "INTO")
        if isinstance(node, exp.Lock):
            return _reject(m.FORBIDDEN_CLAUSE, "FOR UPDATE / FOR SHARE")

    # 6. No forbidden functions
    banned = FORBIDDEN_FUNCTIONS[db_type]
    for node in stmt.find_all(exp.Func):
        name = _function_name(node)
        if name in banned:
            return _reject(m.FORBIDDEN_FUNCTION, name)

    # 7. Row limit, then the final SQL
    changes: list[str] = []
    _apply_limit(stmt, settings, changes)
    final_sql = stmt.sql(dialect=dialect, comments=False)

    # What we hand over must itself be one plain query (guards the rewrite).
    if not _is_single_query(final_sql, dialect):
        return _reject(m.SYNTAX_ERROR)

    return ValidationResult(ok=True, query=ValidatedQuery(final_sql, changes, _seal=m._SEAL))


# --- helpers ---

def _reject(reason: str, detail: str | None = None) -> ValidationResult:
    message = MESSAGES[reason]
    if detail:
        message += f' ("{_safe(detail)}")'
    return ValidationResult(ok=False, reason=reason, message=message)


def _safe(text: str) -> str:
    """Quote-safe, short, no odd characters: a name from the SQL can't
    smuggle anything into the message."""
    return re.sub(r"[^A-Za-z0-9_ /]", "", text)[:40]


def _is_query(stmt: exp.Expression) -> bool:
    """A SELECT, or selects joined by UNION / INTERSECT / EXCEPT
    (WITH ... SELECT is a Select with a WITH). Brackets around one are fine."""
    while isinstance(stmt, exp.Subquery):
        stmt = stmt.this
    return isinstance(stmt, (exp.Select, exp.SetOperation))


def _is_single_query(sql: str, dialect: str) -> bool:
    try:
        parsed = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except (SqlglotError, RecursionError):
        return False
    return len(parsed) == 1 and _is_query(parsed[0])


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def _has_file_output(sql: str, dialect: str) -> bool:
    """INTO OUTFILE / DUMPFILE, found with the tokenizer (so words inside
    strings and comments don't count)."""
    try:
        tokens = Dialect.get_or_raise(dialect).tokenize(sql)
    except Exception:
        return False
    for first, second in zip(tokens, tokens[1:]):
        if first.text.upper() == "INTO" and second.text.upper() in ("OUTFILE", "DUMPFILE"):
            return True
    return False


# --- the row limit (only the outermost LIMIT matters) ---

def _limit_value(limit: exp.Expression) -> int | None:
    """The number in a plain 'LIMIT n' or 'FETCH FIRST n ROWS ONLY', else None."""
    if isinstance(limit, exp.Fetch):
        options = limit.args.get("limit_options")
        if options is not None and (options.args.get("percent") or options.args.get("with_ties")):
            return None
        value = limit.args.get("count")
    else:
        value = limit.args.get("expression")
    if isinstance(value, exp.Literal) and not value.is_string and value.this.isdigit():
        return int(value.this)
    return None


def _apply_limit(stmt: exp.Expression, settings: QuerySettings, changes: list[str]) -> None:
    limit = stmt.args.get("limit")
    if limit is None:
        stmt.limit(settings.default_limit, copy=False)
        changes.append(f"Added LIMIT {settings.default_limit}")
        return

    value = _limit_value(limit)
    if value is None:
        # Not a plain number (for example a negative number, which means
        # "no limit" in SQLite). Replace it with the default.
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(settings.default_limit)))
        changes.append(f"Replaced LIMIT with LIMIT {settings.default_limit}")
    elif value > settings.max_limit:
        stmt.set("limit", exp.Limit(expression=exp.Literal.number(settings.max_limit)))
        changes.append(f"Lowered LIMIT from {value} to {settings.max_limit}")
    # at or below the maximum: left alone. An existing OFFSET is kept.
