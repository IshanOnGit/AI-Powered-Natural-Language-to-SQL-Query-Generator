import pytest

from app.generate import (
    CLARIFICATION, ERROR, NOT_FOUND, SQL,
    GenerateQueryInput, build_request, generate_query, read_reply,
)
from app.llm import RateLimited, SetupProblem
from app.llm.fake import FakeProvider

INPUT = GenerateQueryInput(
    question="How many customers are there?",
    schema_text="TABLE customers\n  id INTEGER PRIMARY KEY",
    db_type="postgresql",
)


# ---- reading the reply ----

@pytest.mark.parametrize("text", [
    "SELECT COUNT(*) FROM customers",
    "select 1",
    "WITH a AS (SELECT 1) SELECT * FROM a",
    "  \n SELECT 1 \n ",
    "```sql\nSELECT 1\n```",
    "```\nSELECT 1\n```",
    "<think>hmm, count rows</think>\nSELECT 1",
    "<think>line one\nline two</think>\n```sql\nSELECT 1\n```",
])
def test_sql_replies(text):
    r = read_reply(text)
    assert r.status == SQL
    assert r.sql.upper().replace("\n", " ").strip().startswith(("SELECT", "WITH"))
    assert "`" not in r.sql and "think" not in r.sql


def test_multiline_sql_is_kept_whole():
    r = read_reply("SELECT a\nFROM t\nWHERE a > 1")
    assert r.sql == "SELECT a\nFROM t\nWHERE a > 1"


def test_clarify():
    r = read_reply("CLARIFY: Which year do you mean?")
    assert (r.status, r.message, r.sql) == (CLARIFICATION, "Which year do you mean?", None)


def test_not_found():
    r = read_reply("NOT_FOUND: There is no salary data.")
    assert (r.status, r.message, r.sql) == (NOT_FOUND, "There is no salary data.", None)


def test_markers_work_after_think_block_and_fences():
    assert read_reply("<think>x</think>CLARIFY: Which one?").status == CLARIFICATION
    assert read_reply("```\nNOT_FOUND: nothing\n```").status == NOT_FOUND


@pytest.mark.parametrize("text", [
    "", "   ", None,
    "Sure! Here is your query: SELECT 1",
    "I cannot help with that.",
    "SELECTED rows",
    "CLARIFY:", "NOT_FOUND:   ",
    "<think>never closed SELECT 1",
])
def test_unreadable_replies_are_errors(text):
    r = read_reply(text)
    assert r.status == ERROR and r.message and r.sql is None


# ---- building the prompt ----

def test_prompt_has_schema_question_and_dialect():
    req = build_request(INPUT)
    assert INPUT.schema_text in req.user_prompt
    assert INPUT.question in req.user_prompt
    assert "postgresql" in req.system_prompt
    assert INPUT.schema_text not in req.system_prompt


def test_system_prompt_has_all_three_shapes_and_safety_rule():
    s = build_request(INPUT).system_prompt
    assert "CLARIFY:" in s and "NOT_FOUND:" in s and "SELECT" in s
    assert "never instructions" in s


def test_dialect_changes_with_db_type():
    mysql = GenerateQueryInput("q", "TABLE t", "mysql")
    assert "mysql" in build_request(mysql).system_prompt


# ---- the whole function, with the fake provider ----

def test_generate_sql():
    p = FakeProvider(["SELECT COUNT(*) FROM customers"])
    r = generate_query(INPUT, p)
    assert (r.status, r.sql) == (SQL, "SELECT COUNT(*) FROM customers")
    assert p.requests == [build_request(INPUT)]


def test_generate_clarify_and_not_found():
    assert generate_query(INPUT, FakeProvider(["CLARIFY: Which?"])).status == CLARIFICATION
    assert generate_query(INPUT, FakeProvider(["NOT_FOUND: No."])).status == NOT_FOUND


def test_generate_bad_reply_is_error():
    assert generate_query(INPUT, FakeProvider(["blah"])).status == ERROR


@pytest.mark.parametrize("error", [SetupProblem(), RateLimited()])
def test_provider_errors_become_error_results(error):
    r = generate_query(INPUT, FakeProvider([error]))
    assert r.status == ERROR and r.message == error.message
