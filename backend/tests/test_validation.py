"""Query validation tests. Plain strings only: no database or model needed
(except the last section, which uses the small Chinook SQLite file)."""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

from app.errors import AppError
from app.validation import (
    EMPTY, FORBIDDEN_CLAUSE, FORBIDDEN_FUNCTION, MULTIPLE_STATEMENTS, NOT_READ_ONLY,
    SYNTAX_ERROR, ValidatedQuery, validate,
)

ALL = ["postgresql", "mysql", "sqlite"]
CHINOOK = Path(__file__).parents[2] / "benchmark" / "fixtures" / "chinook.sqlite"


def ok(sql, db="postgresql"):
    r = validate(sql, db)
    assert r.ok, f"{db}: should be accepted but got {r.reason}: {sql}"
    assert isinstance(r.query, ValidatedQuery) and r.reason is None and r.message is None
    return r.query


def rejected(sql, reason, db="postgresql"):
    r = validate(sql, db)
    assert not r.ok, f"{db}: should be rejected: {sql}"
    assert r.reason == reason, f"{db}: expected {reason}, got {r.reason}: {sql}"
    assert r.query is None and r.message
    return r


# --- must be accepted (and not wrongly rejected) ---

@pytest.mark.parametrize("db", ALL)
@pytest.mark.parametrize("sql", [
    "SELECT * FROM t",
    "SELECT a, b FROM t WHERE a > 1 ORDER BY b",
    "SELECT c.name, COUNT(*) FROM c JOIN o ON o.cid = c.id GROUP BY c.name HAVING COUNT(*) > 2",
    "SELECT * FROM t WHERE id IN (SELECT id FROM u)",
    "SELECT * FROM (SELECT a FROM t) AS s",
    "WITH x AS (SELECT a FROM t) SELECT * FROM x",
    "SELECT a FROM t UNION SELECT a FROM u",
    "SELECT a FROM t INTERSECT SELECT a FROM u",
    "SELECT a FROM t EXCEPT SELECT a FROM u",
    "SELECT 'a; DROP TABLE x'",
    "SELECT update_date, delete_flag, drop_count FROM t",
    "SELECT 1 /* ; DROP TABLE x */",
    "SELECT 1 -- ; DROP TABLE x",
    "SELECT 1;",
    "  select 1  ;  ",
    "(SELECT 1)",
])
def test_good_queries_are_accepted(db, sql):
    ok(sql, db)


def test_quoting_styles_of_each_database():
    ok('SELECT "Name" FROM "Album"', "postgresql")
    ok("SELECT `Name` FROM `Album`", "mysql")
    ok('SELECT "Name" FROM "Album"', "sqlite")
    ok("SELECT `Name` FROM `Album`", "sqlite")


# --- rule 1-3: empty, syntax, several statements ---

@pytest.mark.parametrize("db", ALL)
@pytest.mark.parametrize("sql", ["", "   ", "\n\t", "-- just a comment", "/* only */", ";", None])
def test_empty_input(db, sql):
    rejected(sql, EMPTY, db)


@pytest.mark.parametrize("db", ALL)
@pytest.mark.parametrize("sql", ["hello world, this is not sql", "SELECT FROM WHERE", "SELEC 1", "SELECT * FROM t WHERE ("])
def test_text_that_is_not_sql(db, sql):
    r = validate(sql, db)
    assert not r.ok and r.reason in (SYNTAX_ERROR, NOT_READ_ONLY)


@pytest.mark.parametrize("db", ALL)
@pytest.mark.parametrize("sql", [
    "SELECT 1; DROP TABLE x",
    "SELECT 1; SELECT 2",
    "SELECT 1;; SELECT 2",
    "DROP TABLE x; SELECT 1",
])
def test_two_statements(db, sql):
    rejected(sql, MULTIPLE_STATEMENTS, db)


# --- rule 4: only read-only queries ---

@pytest.mark.parametrize("db", ALL)
@pytest.mark.parametrize("sql", [
    "DROP TABLE x",
    "DELETE FROM x",
    "DELETE FROM x WHERE a = 1",
    "UPDATE x SET a = 1",
    "INSERT INTO x VALUES (1)",
    "INSERT INTO x SELECT * FROM y",
    "TRUNCATE TABLE x",
    "ALTER TABLE x ADD COLUMN b INT",
    "CREATE TABLE x (a INT)",
    "CREATE VIEW v AS SELECT 1",
    "REPLACE INTO x VALUES (1)",
    "VALUES (1), (2)",
    "GRANT ALL ON x TO bob",
    "BEGIN",
    "COMMIT",
])
def test_writes_and_other_statements_are_not_read_only(db, sql):
    r = validate(sql, db)
    assert not r.ok and r.reason in (NOT_READ_ONLY, SYNTAX_ERROR)


@pytest.mark.parametrize("db, sql", [
    ("postgresql", "COPY t TO '/tmp/x'"),
    ("postgresql", "SET search_path = x"),
    ("postgresql", "SHOW all"),
    ("postgresql", "EXPLAIN ANALYZE DELETE FROM t"),
    ("mysql", "SHOW TABLES"),
    ("mysql", "SET @a = 1"),
    ("mysql", "DESCRIBE t"),
    ("mysql", "EXPLAIN SELECT 1"),
    ("sqlite", "ATTACH DATABASE 'x.db' AS y"),
    ("sqlite", "PRAGMA writable_schema = 1"),
    ("sqlite", "PRAGMA table_info(t)"),
    ("sqlite", "VACUUM"),
])
def test_dialect_specific_non_selects(db, sql):
    rejected(sql, NOT_READ_ONLY, db)


@pytest.mark.parametrize("db", ["postgresql", "sqlite"])
def test_write_hidden_inside_a_cte(db):
    rejected("WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d", NOT_READ_ONLY, db)
    rejected("WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT * FROM i", NOT_READ_ONLY, db)
    rejected("WITH u AS (UPDATE t SET a = 1 RETURNING *) SELECT * FROM u", NOT_READ_ONLY, db)


def test_write_at_top_level_after_a_with():
    rejected("WITH x AS (SELECT 1) DELETE FROM t WHERE a IN (SELECT * FROM x)", NOT_READ_ONLY)


# --- rule 5: forbidden clauses ---

def test_select_into():
    rejected("SELECT * INTO new_table FROM t", FORBIDDEN_CLAUSE, "postgresql")
    rejected("SELECT * INTO TEMP new_table FROM t", FORBIDDEN_CLAUSE, "postgresql")


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t INTO OUTFILE '/tmp/x'",
    "SELECT * FROM t INTO DUMPFILE '/tmp/x'",
    "select a from t into  outfile '/tmp/x' fields terminated by ','",
])
def test_mysql_file_output(sql):
    rejected(sql, FORBIDDEN_CLAUSE, "mysql")


def test_outfile_words_inside_strings_are_harmless():
    ok("SELECT 'INTO OUTFILE /tmp/x' FROM t", "mysql")
    ok("SELECT 1 /* INTO OUTFILE '/tmp/x' */", "mysql")


@pytest.mark.parametrize("db, sql", [
    ("postgresql", "SELECT * FROM t FOR UPDATE"),
    ("postgresql", "SELECT * FROM t FOR SHARE"),
    ("postgresql", "SELECT * FROM t FOR NO KEY UPDATE"),
    ("postgresql", "SELECT * FROM t WHERE id IN (SELECT id FROM u FOR UPDATE)"),
    ("mysql", "SELECT * FROM t FOR UPDATE"),
    ("mysql", "SELECT * FROM t LOCK IN SHARE MODE"),
])
def test_row_locking(db, sql):
    rejected(sql, FORBIDDEN_CLAUSE, db)


# --- rule 6: forbidden functions ---

@pytest.mark.parametrize("db, sql", [
    ("postgresql", "SELECT pg_sleep(100)"),
    ("postgresql", "SELECT PG_SLEEP(100)"),
    ("postgresql", "SELECT pg_catalog.pg_sleep(1)"),
    ("postgresql", 'SELECT "pg_sleep"(1)'),
    ("postgresql", "SELECT pg_read_file('/etc/passwd')"),
    ("postgresql", "SELECT set_config('x', 'y', false)"),
    ("postgresql", "SELECT lo_import('/etc/passwd')"),
    ("postgresql", "SELECT lo_export(1234, '/tmp/x')"),
    ("postgresql", "SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)"),
    ("postgresql", "SELECT a FROM t WHERE a = (SELECT pg_sleep(1))"),
    ("postgresql", "WITH x AS (SELECT pg_sleep(1)) SELECT * FROM x"),
    ("mysql", "SELECT SLEEP(5)"),
    ("mysql", "SELECT sleep(5)"),
    ("mysql", "SELECT BENCHMARK(1000000, MD5('a'))"),
    ("mysql", "SELECT load_file('/etc/passwd')"),
    ("mysql", "SELECT a FROM t WHERE a = IF(1, SLEEP(2), 0)"),
    ("sqlite", "SELECT load_extension('x')"),
    ("sqlite", "SELECT readfile('/etc/passwd')"),
    ("sqlite", "SELECT writefile('/tmp/x', 'data')"),
])
def test_forbidden_functions(db, sql):
    rejected(sql, FORBIDDEN_FUNCTION, db)


def test_function_message_names_the_function_safely():
    r = rejected("SELECT pg_sleep(1)", FORBIDDEN_FUNCTION)
    assert r.message.endswith('("pg_sleep")')


def test_denylist_is_per_database():
    ok("SELECT pg_sleep(1)", "sqlite")      # not a SQLite function: fails at run time instead
    ok("SELECT sleep(1)", "postgresql")


def test_function_names_inside_strings_or_columns_are_fine():
    ok("SELECT 'pg_sleep(10)', sleep_time FROM t", "postgresql")
    ok("SELECT sleep FROM t", "mysql")


# --- the row limit ---

def test_limit_is_added_when_missing():
    q = ok("SELECT * FROM t")
    assert q.sql == "SELECT * FROM t LIMIT 100"
    assert q.changes == ["Added LIMIT 100"]


def test_limit_above_the_maximum_is_lowered():
    q = ok("SELECT * FROM t LIMIT 5000")
    assert q.sql == "SELECT * FROM t LIMIT 1000"
    assert q.changes == ["Lowered LIMIT from 5000 to 1000"]


@pytest.mark.parametrize("n", [0, 1, 50, 1000])
def test_limit_at_or_below_the_maximum_is_untouched(n):
    q = ok(f"SELECT * FROM t LIMIT {n}")
    assert q.sql == f"SELECT * FROM t LIMIT {n}" and q.changes == []


def test_limit_only_inside_a_subquery_still_gets_an_outer_limit():
    q = ok("SELECT * FROM (SELECT * FROM t LIMIT 5) AS s")
    assert q.sql.endswith("LIMIT 5) AS s LIMIT 100")
    assert q.changes == ["Added LIMIT 100"]


def test_big_limit_inside_a_subquery_is_not_the_outer_limit():
    q = ok("SELECT * FROM (SELECT * FROM t LIMIT 9999) AS s LIMIT 10")
    assert "LIMIT 9999" in q.sql and q.sql.endswith("LIMIT 10") and q.changes == []


@pytest.mark.parametrize("db", ALL)
def test_union_gets_one_limit_on_the_whole_result(db):
    q = ok("SELECT a FROM t UNION SELECT a FROM u", db)
    assert q.sql == "SELECT a FROM t UNION SELECT a FROM u LIMIT 100"
    q = ok("SELECT a FROM t UNION SELECT a FROM u LIMIT 7", db)
    assert q.sql.endswith("LIMIT 7") and q.changes == []
    q = ok("SELECT a FROM t UNION SELECT a FROM u LIMIT 50000", db)
    assert q.sql.endswith("LIMIT 1000")


@pytest.mark.parametrize("db", ALL)
def test_offset_survives(db):
    q = ok("SELECT * FROM t LIMIT 5000 OFFSET 20", db)
    assert q.sql == "SELECT * FROM t LIMIT 1000 OFFSET 20"
    q = ok("SELECT * FROM t LIMIT 10 OFFSET 20", db)
    assert q.sql == "SELECT * FROM t LIMIT 10 OFFSET 20"


def test_mysql_comma_limit():
    q = ok("SELECT * FROM t LIMIT 20, 5000", "mysql")
    assert q.sql == "SELECT * FROM t LIMIT 1000 OFFSET 20"


def test_fetch_first_is_a_limit_too():
    assert ok("SELECT * FROM t FETCH FIRST 5 ROWS ONLY").changes == []
    q = ok("SELECT * FROM t FETCH FIRST 9999 ROWS ONLY")
    assert q.sql.endswith("LIMIT 1000") and "FETCH" not in q.sql
    q = ok("SELECT * FROM t FETCH FIRST 10 PERCENT ROWS ONLY")
    assert q.sql.endswith("LIMIT 100") and "PERCENT" not in q.sql


def test_negative_limit_means_no_limit_in_sqlite_so_it_is_replaced():
    q = ok("SELECT * FROM t LIMIT -1", "sqlite")
    assert q.sql == "SELECT * FROM t LIMIT 100"
    assert q.changes == ["Replaced LIMIT with LIMIT 100"]


def test_limit_all_never_reaches_the_database_unbounded():
    r = validate("SELECT * FROM t LIMIT ALL", "postgresql")
    assert (not r.ok) or (r.query.sql == "SELECT * FROM t LIMIT 100" and "ALL" not in r.query.sql)


def test_limit_with_cte_and_order_by():
    q = ok("WITH x AS (SELECT a FROM t) SELECT a FROM x ORDER BY a DESC")
    assert q.sql.endswith("ORDER BY a DESC LIMIT 100")


def test_limits_come_from_settings(monkeypatch):
    monkeypatch.setenv("QUERY_DEFAULT_LIMIT", "5")
    monkeypatch.setenv("QUERY_MAX_LIMIT", "20")
    assert ok("SELECT * FROM t").sql.endswith("LIMIT 5")
    assert ok("SELECT * FROM t LIMIT 99").sql.endswith("LIMIT 20")


@pytest.mark.parametrize("default, maximum", [("abc", "10"), ("0", "10"), ("10", "-5"), ("50", "10")])
def test_bad_limit_settings_are_reported_clearly(monkeypatch, default, maximum):
    monkeypatch.setenv("QUERY_DEFAULT_LIMIT", default)
    monkeypatch.setenv("QUERY_MAX_LIMIT", maximum)
    with pytest.raises(AppError) as e:
        validate("SELECT 1", "sqlite")
    assert e.value.code == "query_setup_problem"


# --- the final SQL ---

def test_comments_and_trailing_semicolon_are_removed():
    q = ok("SELECT a /* hi */ FROM t -- bye\n;")
    assert q.sql == "SELECT a FROM t LIMIT 100"


def test_final_sql_is_valid_for_the_dialect_and_is_stable():
    for db, sql in [("postgresql", 'select "A" from "T" where x = 1'),
                    ("mysql", "select `A` from `T` where x = 1"),
                    ("sqlite", 'select "A" from "T" where x = 1')]:
        first = ok(sql, db)
        again = ok(first.sql, db)          # validating the result changes nothing
        assert again.sql == first.sql and again.changes == []


@pytest.mark.parametrize("sql", [
    "SELECT 1 /*!50000 ; DROP TABLE x */",
    "SELECT 1 /*! UNION SELECT SLEEP(5) */",
    "SELECT /*!50000 SLEEP(5) */ 1",
])
def test_mysql_versioned_comments_never_reach_the_database(sql):
    # MySQL runs the inside of /*! ... */. We drop every comment when we
    # rebuild the SQL, so what runs is only what we checked.
    q = ok(sql, "mysql")
    assert q.sql == "SELECT 1 LIMIT 100" and "/*" not in q.sql


def test_strings_with_semicolons_survive_unchanged():
    assert "'a; DROP TABLE x'" in ok("SELECT 'a; DROP TABLE x'").sql


# --- only validate() can make a ValidatedQuery ---

def test_validated_query_cannot_be_built_by_hand():
    with pytest.raises(TypeError):
        ValidatedQuery("DROP TABLE x", [])
    with pytest.raises(TypeError):
        ValidatedQuery("DROP TABLE x", [], _seal=object())


def test_validated_query_is_read_only():
    q = ok("SELECT 1")
    with pytest.raises(Exception):
        q.sql = "DROP TABLE x"


def test_unknown_db_type_is_a_programming_error():
    with pytest.raises(ValueError):
        validate("SELECT 1", "oracle")


def test_messages_are_fixed_text_and_never_echo_the_sql():
    r = rejected("DROP TABLE secret_table_name", NOT_READ_ONLY)
    assert "secret_table_name" not in r.message
    r = rejected("SELECT 1; DROP TABLE secret_table_name", MULTIPLE_STATEMENTS)
    assert "secret_table_name" not in r.message


# --- the second lock, on its own (SQLite, real file) ---

@pytest.fixture
def chinook():
    from app.connection import ConnectionInput, connect
    conn = connect(ConnectionInput("sqlite", database=str(CHINOOK)))
    yield conn
    conn.close()


def test_accepted_queries_run_on_chinook_and_respect_the_limit(chinook):
    q = ok("SELECT Name FROM Genre ORDER BY Name", "sqlite")
    with chinook.engine.connect() as c:
        rows = c.execute(text(q.sql)).fetchall()
    assert 0 < len(rows) <= 100
    q = ok("SELECT TrackId FROM Track", "sqlite")
    with chinook.engine.connect() as c:
        assert len(c.execute(text(q.sql)).fetchall()) == 100  # Track has 3503 rows


WRITES_THE_VALIDATOR_BLOCKS = [
    "INSERT INTO Genre (GenreId, Name) VALUES (9999, 'x')",
    "UPDATE Genre SET Name = 'x'",
    "DELETE FROM Genre",
    "DROP TABLE Genre",
    "CREATE TABLE sneaky (a INTEGER)",
    "ALTER TABLE Genre ADD COLUMN extra TEXT",
]


@pytest.mark.parametrize("sql", WRITES_THE_VALIDATOR_BLOCKS)
def test_validator_blocks_it_and_the_read_only_connection_blocks_it_too(chinook, sql):
    assert not validate(sql, "sqlite").ok                      # lock 1
    with chinook.engine.connect() as c:                        # lock 2, on its own
        with pytest.raises(OperationalError):
            c.execute(text(sql))
    with chinook.engine.connect() as c:
        assert c.execute(text("SELECT COUNT(*) FROM Genre")).scalar() == 25
