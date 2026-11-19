"""Connection tests. SQLite files need no server. PostgreSQL / MySQL checks that
need a real server are at the bottom and run only if you set the TEST_* variables
(see the README)."""
import logging
import os
import pickle
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, OperationalError, ProgrammingError

from app.connection import (
    AuthFailed, Connection, ConnectionInput, DatabaseNotFound, DBConnectionError,
    InvalidInput, SecretStr, Unknown, Unreachable, UnsupportedDatabase, connect,
)
from app.connection import connection as conn_module
from app.errors import AppError
from app.schema import extract_schema

CHINOOK = Path(__file__).parents[2] / "benchmark" / "fixtures" / "chinook.sqlite"
PASSWORD = "p@ss/w#rd-TOPSECRET"


def sqlite_input(path) -> ConnectionInput:
    return ConnectionInput("sqlite", database=str(path))


def server_input(db_type="postgresql", **overrides) -> ConnectionInput:
    values = dict(db_type=db_type, host="127.0.0.1", port=1, database="d",
                  username="u", password=SecretStr(PASSWORD))
    values.update(overrides)
    return ConnectionInput(**values)


# --- SecretStr ---

def test_secret_never_shows_in_print_format_or_repr():
    s = SecretStr(PASSWORD)
    for shown in (str(s), repr(s), f"{s}", "%s" % s, "{}".format(s), repr([s]), repr({"k": s})):
        assert PASSWORD not in shown and "***" in shown
    assert s.get_secret_value() == PASSWORD


def test_secret_is_read_only_and_cannot_be_pickled():
    s = SecretStr("x")
    with pytest.raises(AttributeError):
        s.value = "y"
    with pytest.raises(TypeError):
        pickle.dumps(s)


def test_input_wraps_plain_password_and_hides_it_in_repr():
    i = ConnectionInput("postgresql", host="h", database="d", username="u", password=PASSWORD)
    assert isinstance(i.password, SecretStr)
    assert PASSWORD not in repr(i) and PASSWORD not in str(i)


# --- checking the input ---

@pytest.mark.parametrize("db_type", ["oracle", "", None, "sqlserver"])
def test_unsupported_database(db_type):
    with pytest.raises(UnsupportedDatabase):
        connect(ConnectionInput(db_type))


@pytest.mark.parametrize("db_type, missing, message", [
    ("postgresql", "host", "Host is required for PostgreSQL."),
    ("postgresql", "database", "Database name is required for PostgreSQL."),
    ("postgresql", "username", "Username is required for PostgreSQL."),
    ("mysql", "host", "Host is required for MySQL."),
    ("mysql", "database", "Database name is required for MySQL."),
    ("mysql", "username", "Username is required for MySQL."),
])
def test_each_missing_required_field(db_type, missing, message):
    fields = dict(host="h", database="d", username="u")
    fields[missing] = None
    with pytest.raises(InvalidInput) as e:
        connect(ConnectionInput(db_type, **fields))
    assert e.value.message == message


def test_blank_fields_count_as_missing():
    with pytest.raises(InvalidInput):
        connect(server_input(host="   "))


def test_sqlite_needs_a_file_path():
    with pytest.raises(InvalidInput) as e:
        connect(ConnectionInput("sqlite"))
    assert "SQLite" in e.value.message


@pytest.mark.parametrize("port", [0, -1, 70000, "5432"])
def test_bad_port(port):
    with pytest.raises(InvalidInput):
        connect(server_input(port=port))


def test_db_type_is_case_insensitive():
    c = connect(ConnectionInput("SQLite", database=str(CHINOOK)))
    assert c.db_type == "sqlite"
    c.close()


# --- SQLite ---

def test_sqlite_connects_and_schema_can_be_read():
    c = connect(sqlite_input(CHINOOK))
    try:
        assert c.db_type == "sqlite" and not c.closed
        assert len(extract_schema(c).tables) == 11
    finally:
        c.close()


def test_sqlite_is_read_only():
    c = connect(sqlite_input(CHINOOK))
    try:
        with c.engine.connect() as raw:
            with pytest.raises(OperationalError) as e:
                raw.execute(text("INSERT INTO Genre (GenreId, Name) VALUES (9999, 'x')"))
            assert "readonly" in str(e.value).lower()
            with pytest.raises(OperationalError):
                raw.execute(text("CREATE TABLE sneaky (a INTEGER)"))
            with pytest.raises(OperationalError):
                raw.execute(text("DROP TABLE Genre"))
    finally:
        c.close()


def test_sqlite_missing_file(tmp_path):
    with pytest.raises(DatabaseNotFound) as e:
        connect(sqlite_input(tmp_path / "nope.sqlite"))
    assert "was not found" in e.value.message
    assert not (tmp_path / "nope.sqlite").exists()  # read-only mode must not create it


def test_sqlite_file_that_is_not_a_database(tmp_path):
    bad = tmp_path / "bad.sqlite"
    bad.write_text("this is just text, not a database" * 20)
    with pytest.raises(InvalidInput) as e:
        connect(sqlite_input(bad))
    assert "not a valid SQLite database" in e.value.message


def test_sqlite_directory_is_not_a_file(tmp_path):
    with pytest.raises(DatabaseNotFound):
        connect(sqlite_input(tmp_path))


@pytest.mark.parametrize("name", ["with space.sqlite", "hash#and%20pct.sqlite", "q?mark&amp.sqlite", "ünï.sqlite"])
def test_sqlite_odd_file_names(tmp_path, name):
    path = tmp_path / name
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE t (a INTEGER)")
    db.commit()
    db.close()
    c = connect(sqlite_input(path))
    try:
        assert [t.name for t in extract_schema(c).tables] == ["t"]
    finally:
        c.close()


def test_close_forgets_the_engine_and_can_repeat():
    c = connect(sqlite_input(CHINOOK))
    c.close()
    assert c.closed and c.engine is None
    c.close()  # no error the second time


# --- how the URL and engine are built (no server needed) ---

def test_special_characters_in_password_survive_the_url():
    url = conn_module._build_url(server_input("postgresql"))
    assert url.password == PASSWORD
    again = make_url(url.render_as_string(hide_password=False))
    assert (again.password, again.host, again.port, again.username) == (PASSWORD, "127.0.0.1", 1, "u")
    assert PASSWORD not in url.render_as_string()  # hidden by default


@pytest.mark.parametrize("db_type, driver, port", [
    ("postgresql", "postgresql+psycopg2", 5432),
    ("mysql", "mysql+pymysql", 3306),
])
def test_default_ports_and_drivers(db_type, driver, port):
    url = conn_module._build_url(server_input(db_type, port=None))
    assert url.drivername == driver and url.port == port
    assert conn_module._build_url(server_input(db_type, port=4000)).port == 4000


def test_connect_timeout_and_read_only_settings():
    pg = conn_module._connect_args("postgresql")
    assert pg["connect_timeout"] == 10 and "default_transaction_read_only=on" in pg["options"]
    my = conn_module._connect_args("mysql")
    assert my["connect_timeout"] == 10 and my["init_command"] == "SET SESSION TRANSACTION READ ONLY"


def test_engine_has_a_minimal_pool():
    engine = conn_module._make_engine(server_input("postgresql"))
    try:
        assert engine.pool.size() == 1 and engine.pool._max_overflow == 0
    finally:
        engine.dispose()


# --- real failures, offline ---

@pytest.mark.parametrize("db_type", ["postgresql", "mysql"])
@pytest.mark.parametrize("host, port", [("127.0.0.1", 1), ("no-such-host.invalid", None)])
def test_unreachable_server(db_type, host, port):
    with pytest.raises(Unreachable) as e:
        connect(server_input(db_type, host=host, port=port))
    assert "Can't reach" in e.value.message


# --- error translation (driver errors faked) ---

class FakeDriverError(Exception):
    def __init__(self, *args, pgcode=None):
        super().__init__(*args)
        self.pgcode = pgcode


def failing_test(error: Exception):
    def run(engine, db_type):
        raise error
    return run


@pytest.mark.parametrize("db_type, driver_error, expected", [
    ("postgresql", FakeDriverError(f'FATAL: password authentication failed for user "u" {PASSWORD}', pgcode="28P01"), AuthFailed),
    ("postgresql", FakeDriverError('FATAL: no pg_hba.conf entry for host'), AuthFailed),
    ("postgresql", FakeDriverError('FATAL: database "d" does not exist', pgcode="3D000"), DatabaseNotFound),
    ("postgresql", FakeDriverError('FATAL: database "d" does not exist'), DatabaseNotFound),
    ("postgresql", FakeDriverError('connection to server failed: timeout expired'), Unreachable),
    ("postgresql", FakeDriverError('something odd'), Unknown),
    ("mysql", FakeDriverError(1045, f"Access denied for user 'u' (using password: YES) {PASSWORD}"), AuthFailed),
    ("mysql", FakeDriverError(1049, "Unknown database 'd'"), DatabaseNotFound),
    ("mysql", FakeDriverError(2003, "Can't connect to MySQL server on 'h'"), Unreachable),
    ("mysql", FakeDriverError(9999, "weird"), Unknown),
])
def test_driver_errors_become_simple_errors(monkeypatch, db_type, driver_error, expected):
    monkeypatch.setattr(conn_module, "_test", failing_test(OperationalError("SELECT 1", {}, driver_error)))
    with pytest.raises(expected):
        connect(server_input(db_type, database="d"))


def test_database_not_found_message_names_the_database(monkeypatch):
    monkeypatch.setattr(conn_module, "_test", failing_test(
        OperationalError("x", {}, FakeDriverError(1049, "Unknown database"))))
    with pytest.raises(DatabaseNotFound) as e:
        connect(server_input("mysql", database="shop"))
    assert e.value.message == "Database 'shop' was not found."


def test_non_driver_errors_are_unknown(monkeypatch):
    monkeypatch.setattr(conn_module, "_test", failing_test(RuntimeError(PASSWORD)))
    with pytest.raises(Unknown):
        connect(server_input())


def test_errors_are_app_errors_with_db_codes():
    for cls in (InvalidInput, AuthFailed, Unreachable, DatabaseNotFound, UnsupportedDatabase, Unknown):
        e = cls()
        assert isinstance(e, DBConnectionError) and isinstance(e, AppError)
        assert e.code.startswith("db_") and e.message


# --- the password never leaks ---

def test_password_never_leaks_from_any_error(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    leaky = [
        OperationalError("x", {}, FakeDriverError(f"password authentication failed {PASSWORD}", pgcode="28P01")),
        OperationalError("x", {}, FakeDriverError(f'database "d" does not exist {PASSWORD}', pgcode="3D000")),
        OperationalError("x", {}, FakeDriverError(f"could not connect {PASSWORD}")),
        ProgrammingError("x", {}, FakeDriverError(f"weird {PASSWORD}")),
        RuntimeError(f"postgresql://u:{PASSWORD}@h/d"),
    ]
    for error in leaky:
        monkeypatch.setattr(conn_module, "_test", failing_test(error))
        with pytest.raises(DBConnectionError) as e:
            connect(server_input())
        for shown in (str(e.value), repr(e.value), e.value.message):
            assert PASSWORD not in shown
        assert e.value.__cause__ is None
    assert PASSWORD not in caplog.text


@pytest.mark.parametrize("db_type", ["postgresql", "mysql"])
def test_password_never_leaks_from_a_real_failed_connect(db_type, caplog):
    caplog.set_level(logging.DEBUG)
    with pytest.raises(DBConnectionError) as e:
        connect(server_input(db_type))
    assert PASSWORD not in str(e.value) + repr(e.value) + e.value.message + caplog.text


def test_password_not_in_connection_repr(monkeypatch):
    engine = conn_module._make_engine(server_input())
    try:
        assert PASSWORD not in repr(Connection("postgresql", engine))
        assert PASSWORD not in repr(engine)
    finally:
        engine.dispose()


# --- integration tests: real PostgreSQL / MySQL servers ---
# Set TEST_PG_* or TEST_MYSQL_* (HOST, PORT, DB, USER, PASSWORD) to run them.
# Use a throwaway database. Nothing is ever written to it by these tests.

def env_input(prefix: str, db_type: str, **overrides):
    if not os.environ.get(f"{prefix}_HOST"):
        pytest.skip(f"{prefix}_HOST not set")
    values = dict(
        db_type=db_type,
        host=os.environ[f"{prefix}_HOST"],
        port=int(os.environ.get(f"{prefix}_PORT", "0")) or None,
        database=os.environ[f"{prefix}_DB"],
        username=os.environ[f"{prefix}_USER"],
        password=SecretStr(os.environ.get(f"{prefix}_PASSWORD", "")),
    )
    values.update(overrides)
    return ConnectionInput(**values)


SERVERS = [("TEST_PG", "postgresql"), ("TEST_MYSQL", "mysql")]


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_connects_and_is_read_only(prefix, db_type):
    c = connect(env_input(prefix, db_type))
    try:
        with c.engine.connect() as raw:
            assert raw.execute(text("SELECT 1")).scalar() == 1
            with pytest.raises(DBAPIError) as e:
                raw.execute(text("CREATE TABLE _readonly_probe (x INTEGER)"))
            assert "read-only" in str(e.value).lower() or "read only" in str(e.value).lower()
    finally:
        c.close()


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_wrong_password(prefix, db_type):
    with pytest.raises(AuthFailed) as e:
        connect(env_input(prefix, db_type, password=SecretStr("definitely-wrong-pw-123")))
    assert "definitely-wrong-pw-123" not in str(e.value)


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_wrong_database(prefix, db_type):
    with pytest.raises(DatabaseNotFound):
        connect(env_input(prefix, db_type, database="no_such_database_xyz"))


@pytest.mark.integration
@pytest.mark.parametrize("prefix, db_type", SERVERS)
def test_real_server_wrong_port(prefix, db_type):
    with pytest.raises(Unreachable):
        connect(env_input(prefix, db_type, port=1))
