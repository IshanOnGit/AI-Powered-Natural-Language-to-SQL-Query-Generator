"""Query execution tests. SQLite (Chinook) runs everywhere. PostgreSQL / MySQL
checks need a real server and run only if the TEST_* variables are set."""
import json
import logging
import time
from datetime import date, datetime, time as dtime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import text

from app.connection import ConnectionInput, SecretStr, connect
from app.errors import AppError
from app.execution import (
    ConnectionLost, ExecutionError, QueryFailed, Timeout, Unknown, execute, to_display,
)
from app.validation import ValidatedQuery, validate
from app.validation.models import _SEAL

CHINOOK = Path(__file__).parents[2] / "benchmark" / "fixtures" / "chinook.sqlite"


@pytest.fixture
def chinook():
    conn = connect(ConnectionInput("sqlite", database=str(CHINOOK)))
    yield conn
    conn.close()


def validated(sql, db="sqlite") -> ValidatedQuery:
    r = validate(sql, db)
    assert r.ok, r.message
    return r.query


def hand_built(sql) -> ValidatedQuery:
    """Skips validation. Tests only: to prove the second lock on its own."""
    return ValidatedQuery(sql, [], _seal=_SEAL)


def run(conn, sql):
    return execute(conn, validated(sql))


# --- normal results ---

def test_normal_query(chinook):
    r = run(chinook, "SELECT GenreId, Name FROM Genre ORDER BY GenreId")
    assert r.columns == ["GenreId", "Name"]
    assert r.rows[0] == [1, "Rock"]
    assert r.row_count == len(r.rows) == 25
    assert isinstance(r.elapsed_ms, int) and r.elapsed_ms >= 0


def test_zero_rows_is_a_success_with_columns(chinook):
    r = run(chinook, "SELECT GenreId, Name FROM Genre WHERE Name = 'no such genre'")
    assert r.columns == ["GenreId", "Name"] and r.rows == [] and r.row_count == 0


def test_nulls_become_none(chinook):
    r = run(chinook, "SELECT CustomerId, Company FROM Customer ORDER BY CustomerId")
    assert r.rows[0][1] is not None and r.rows[1][1] is None


def test_date_column_and_real_numbers(chinook):
    r = run(chinook, "SELECT InvoiceDate, Total FROM Invoice ORDER BY InvoiceId")
    import re
    assert re.match(r"^\d{4}-\d\d-\d\d", r.rows[0][0])      # dates are text in SQLite
    assert isinstance(r.rows[0][1], float)


def test_aggregates_and_joins(chinook):
    r = run(chinook, "SELECT g.Name, COUNT(*) AS n FROM Track t JOIN Genre g ON g.GenreId = t.GenreId "
                     "GROUP BY g.Name ORDER BY n DESC")
    assert r.columns == ["Name", "n"] and r.rows[0][0] == "Rock" and r.rows[0][1] == 1297


def test_the_row_limit_added_by_validation_is_what_limits_the_rows(chinook):
    assert run(chinook, "SELECT TrackId FROM Track").row_count == 100


def test_the_whole_result_is_json_safe(chinook):
    r = run(chinook, "SELECT * FROM Customer")
    json.dumps(r.rows, allow_nan=False)


# --- SQL is sent exactly as validated (no % or : surprises) ---

@pytest.mark.parametrize("sql, expected", [
    ("SELECT '100%' AS a, '%d' AS b", ["100%", "%d"]),
    ("SELECT 'time 12:30 :now' AS a", ["time 12:30 :now"]),
    ("SELECT Name FROM Genre WHERE Name LIKE '%ock' AND GenreId = 1", ["Rock"]),
])
def test_percent_and_colon_survive(chinook, sql, expected):
    assert run(chinook, sql).rows[0] == expected


# --- value conversion: one test per row of the spec table ---

def test_value_null():
    assert to_display(None) is None


@pytest.mark.parametrize("value", [0, 7, -3, 2**70, 1.5, -0.25, True, False, "plain text", ""])
def test_value_unchanged(value):
    result = to_display(value)
    assert result == value and type(result) is type(value)


def test_value_bool_stays_bool_not_int():
    assert to_display(True) is True and to_display(False) is False


@pytest.mark.parametrize("value, shown", [
    (Decimal("12.50"), "12.50"),
    (Decimal("0.10"), "0.10"),
    (Decimal("1E+3"), "1000"),
    (Decimal("-0.000001"), "-0.000001"),
    (Decimal("NaN"), "NaN"),
])
def test_value_decimal_becomes_exact_text(value, shown):
    assert to_display(value) == shown


@pytest.mark.parametrize("value, shown", [
    (date(2025, 3, 1), "2025-03-01"),
    (datetime(2025, 3, 1, 14, 5, 9), "2025-03-01T14:05:09"),
    (datetime(2025, 3, 1, 14, 5, 9, 250000), "2025-03-01T14:05:09.250000"),
    (dtime(14, 5, 9), "14:05:09"),
])
def test_value_dates_and_times_become_iso_text(value, shown):
    assert to_display(value) == shown


@pytest.mark.parametrize("value, shown", [
    (b"\x00\x01", "<binary, 2 bytes>"),
    (bytes(2048), "<binary, 2048 bytes>"),
    (bytearray(5), "<binary, 5 bytes>"),
    (memoryview(b"abc"), "<binary, 3 bytes>"),
    (b"", "<binary, 0 bytes>"),
])
def test_value_binary_becomes_a_placeholder(value, shown):
    assert to_display(value) == shown


def test_value_anything_else_becomes_its_text_form():
    uid = UUID("12345678-1234-5678-1234-567812345678")
    assert to_display(uid) == "12345678-1234-5678-1234-567812345678"
    assert to_display([1, 2, 3]) == "[1, 2, 3]"
    assert to_display({"a": 1}) == "{'a': 1}"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_value_non_finite_floats_become_text_so_json_works(value):
    shown = to_display(value)
    assert isinstance(shown, str)
    json.dumps([shown], allow_nan=False)


def test_value_long_text_is_cut_at_1000_characters_with_an_ellipsis():
    shown = to_display("x" * 2000)
    assert shown == "x" * 1000 + "…"
    assert to_display("y" * 1000) == "y" * 1000          # exactly 1000: untouched
    assert to_display("y" * 1001) == "y" * 1000 + "…"


def test_value_long_text_from_other_types_is_cut_too():
    assert to_display(Decimal("1." + "1" * 1500)).endswith("…")
    assert to_display(["z"] * 2000).endswith("…")


def test_long_text_and_binary_through_a_real_query(chinook):
    r = run(chinook, "SELECT hex(zeroblob(1000)) AS long_text, zeroblob(2048) AS bin, 5 AS n")
    assert r.rows[0] == ["0" * 1000 + "…", "<binary, 2048 bytes>", 5]


def test_cutting_long_text_changes_nothing_in_the_database(chinook):
    run(chinook, "SELECT hex(zeroblob(1000))")
    assert run(chinook, "SELECT COUNT(*) FROM Genre").rows == [[25]]


# --- time limit ---

SLOW = "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT COUNT(*) FROM r"


def test_slow_query_is_stopped_near_the_limit_on_sqlite(chinook, monkeypatch):
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", "1")
    started = time.perf_counter()
    with pytest.raises(Timeout) as e:
        run(chinook, SLOW)
    assert time.perf_counter() - started < 5
    assert e.value.code == "query_timeout"


def test_connection_still_works_after_a_timeout(chinook, monkeypatch):
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", "0.5")
    with pytest.raises(Timeout):
        run(chinook, SLOW)
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", "15")
    assert run(chinook, "SELECT COUNT(*) FROM Genre").rows == [[25]]


def test_a_fast_query_is_not_hit_by_the_timer(chinook, monkeypatch):
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", "0.5")
    assert run(chinook, "SELECT 1").rows == [[1]]
    time.sleep(0.8)                                   # the cancelled timer must not fire later
    assert run(chinook, "SELECT 2").rows == [[2]]


@pytest.mark.parametrize("value", ["abc", "0", "-5", "inf", "nan"])
def test_bad_timeout_setting_is_reported_clearly(chinook, monkeypatch, value):
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", value)
    with pytest.raises(AppError) as e:
        run(chinook, "SELECT 1")
    assert e.value.code == "query_setup_problem"


# --- failures ---

def test_wrong_column_gives_query_failed_with_the_database_message(chinook):
    with pytest.raises(QueryFailed) as e:
        run(chinook, "SELECT NoSuchColumn FROM Genre")
    assert "no such column" in e.value.message.lower() and "NoSuchColumn" in e.value.message
    assert e.value.code == "query_failed"


def test_wrong_table(chinook):
    with pytest.raises(QueryFailed) as e:
        run(chinook, "SELECT * FROM nope")
    assert "no such table" in e.value.message.lower()


def test_error_text_is_trimmed_to_300_characters(chinook):
    name = "c" * 600
    with pytest.raises(QueryFailed) as e:
        run(chinook, f"SELECT {name} FROM Genre")
    assert len(e.value.message) <= len("The database couldn't run this query: ") + 300


def test_a_closed_connection_is_connection_lost(chinook):
    chinook.close()
    with pytest.raises(ConnectionLost) as e:
        run(chinook, "SELECT 1")
    assert e.value.code == "query_connection_lost"


def test_a_deleted_database_file_is_reported_not_crashed(tmp_path):
    import shutil
    copy = tmp_path / "copy.sqlite"
    shutil.copy(CHINOOK, copy)
    conn = connect(ConnectionInput("sqlite", database=str(copy)))
    try:
        copy.write_bytes(b"this is no longer a database" * 50)
        with pytest.raises(ExecutionError):
            run(conn, "SELECT * FROM Genre")
    finally:
        conn.close()


def test_unexpected_non_database_errors_are_unknown(chinook, monkeypatch):
    from app.execution import runner as module
    monkeypatch.setattr("sqlalchemy.engine.Connection.exec_driver_sql", lambda *a, **k: 1 / 0)
    with pytest.raises(Unknown):
        run(chinook, "SELECT 1")


# --- safety ---

def test_only_a_validated_query_is_accepted(chinook):
    with pytest.raises(TypeError):
        execute(chinook, "SELECT 1")
    with pytest.raises(TypeError):
        execute(chinook, None)


@pytest.mark.parametrize("sql", [
    "INSERT INTO Genre (GenreId, Name) VALUES (9999, 'x')",
    "UPDATE Genre SET Name = 'x'",
    "DELETE FROM Genre",
    "DROP TABLE Genre",
    "CREATE TABLE sneaky (a INTEGER)",
])
def test_write_that_skips_validation_is_refused_by_the_database_and_changes_nothing(chinook, sql):
    with pytest.raises(QueryFailed) as e:
        execute(chinook, hand_built(sql))
    assert "readonly" in e.value.message.lower().replace("-", "").replace(" ", "")
    assert run(chinook, "SELECT COUNT(*) FROM Genre").rows == [[25]]


def test_nothing_is_ever_committed(chinook):
    run(chinook, "SELECT * FROM Genre")
    with chinook.engine.connect() as c:
        assert c.exec_driver_sql("PRAGMA data_version").scalar() is not None
    assert not Path(str(CHINOOK) + "-journal").exists()


def test_errors_are_app_errors_with_query_codes():
    for cls in (Timeout, QueryFailed, ConnectionLost, Unknown):
        e = cls()
        assert isinstance(e, ExecutionError) and isinstance(e, AppError)
        assert e.code.startswith("query_") and e.message


# --- credentials never appear ---

PASSWORD = "p@ss-TOPSECRET-123"


class FakeDriverError(Exception):
    def __init__(self, *args, pgcode=None):
        super().__init__(*args)
        self.pgcode = pgcode


def test_password_is_scrubbed_from_database_error_text(chinook, monkeypatch):
    from sqlalchemy.exc import ProgrammingError
    from app.execution import runner as module
    chinook.engine = chinook.engine.execution_options()          # same engine, new wrapper
    # pretend the engine's URL carries a password and the driver echoes it back
    monkeypatch.setattr(type(chinook.engine.url), "password", property(lambda self: PASSWORD), raising=False)
    error = ProgrammingError("SELECT", {}, FakeDriverError(f'connection to postgresql://u:{PASSWORD}@host/db failed, pw={PASSWORD}'))
    shown = module._translate(error, chinook, False)
    assert PASSWORD not in shown.message and PASSWORD not in repr(shown)


def test_user_and_password_inside_a_url_are_scrubbed(chinook):
    from sqlalchemy.exc import ProgrammingError
    from app.execution import runner as module
    error = ProgrammingError("SELECT", {}, FakeDriverError("bad: postgresql://someone:hunter2@db.example/x"))
    assert "hunter2" not in module._translate(error, chinook, False).message


# --- error translation (driver errors faked) ---

@pytest.mark.parametrize("driver_error, expected", [
    (FakeDriverError("canceling statement due to statement timeout", pgcode="57014"), Timeout),
    (FakeDriverError(3024, "Query execution was interrupted, maximum statement execution time exceeded"), Timeout),
    (FakeDriverError("server closed the connection unexpectedly"), ConnectionLost),
    (FakeDriverError(2013, "Lost connection to MySQL server during query"), ConnectionLost),
    (FakeDriverError(2006, "MySQL server has gone away"), ConnectionLost),
    (FakeDriverError('column "x" does not exist', pgcode="42703"), QueryFailed),
    (FakeDriverError(1054, "Unknown column 'x' in 'field list'"), QueryFailed),
    (FakeDriverError("cannot execute INSERT in a read-only transaction", pgcode="25006"), QueryFailed),
    (FakeDriverError(1792, "Cannot execute statement in a READ ONLY transaction."), QueryFailed),
])
def test_driver_errors_become_simple_errors(chinook, driver_error, expected):
    from sqlalchemy.exc import OperationalError
    from app.execution import runner as module
    assert isinstance(module._translate(OperationalError("x", {}, driver_error), chinook, False), expected)


def test_mysql_error_text_comes_without_the_code(chinook):
    from sqlalchemy.exc import OperationalError
    from app.execution import runner as module
    shown = module._translate(OperationalError("x", {}, FakeDriverError(1054, "Unknown column 'x' in 'field list'")), chinook, False)
    assert shown.message == "The database couldn't run this query: Unknown column 'x' in 'field list'"


# --- integration: real PostgreSQL / MySQL ---

from tests.test_connection import SERVERS, env_input  # noqa: E402

SLOW_SERVER_QUERY = {
    "postgresql": "SELECT COUNT(*) FROM generate_series(1, 200000000) a, generate_series(1, 1000) b",
    "mysql": "SELECT COUNT(*) FROM information_schema.columns a, information_schema.columns b, information_schema.columns c",
}


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_runs_a_query(prefix, db_type):
    c = connect(env_input(prefix, db_type))
    try:
        r = execute(c, validated("SELECT 1 AS one, 'a' AS letter", db_type))
        assert r.columns == ["one", "letter"] and r.rows == [[1, "a"]]
    finally:
        c.close()


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_stops_a_slow_query(prefix, db_type, monkeypatch):
    monkeypatch.setenv("QUERY_TIMEOUT_SECONDS", "2")
    c = connect(env_input(prefix, db_type))
    try:
        started = time.perf_counter()
        with pytest.raises(Timeout):
            execute(c, validated(SLOW_SERVER_QUERY[db_type], db_type))
        assert time.perf_counter() - started < 10
        assert execute(c, validated("SELECT 1", db_type)).rows == [[1]]   # still usable
    finally:
        c.close()


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_wrong_column_and_refused_write(prefix, db_type):
    c = connect(env_input(prefix, db_type))
    try:
        with pytest.raises(QueryFailed):
            execute(c, validated("SELECT no_such_column_xyz FROM information_schema.tables", db_type))
        with pytest.raises(QueryFailed):
            execute(c, hand_built("CREATE TABLE _readonly_probe (x INTEGER)"))
    finally:
        c.close()


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_value_types(prefix, db_type):
    c = connect(env_input(prefix, db_type))
    try:
        sql = ("SELECT CAST(12.50 AS DECIMAL(10,2)) AS d, CAST('2025-03-01' AS DATE) AS dt, NULL AS n")
        r = execute(c, validated(sql, db_type))
        assert r.rows == [["12.50", "2025-03-01", None]]
    finally:
        c.close()
