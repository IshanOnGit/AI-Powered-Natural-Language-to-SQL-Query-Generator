from app.llm import SetupProblem
from app.llm.fake import FakeProvider
from app.pipeline import (
    CLARIFICATION, ERROR, NOT_FOUND, READY, REJECTED, generate_validated_query,
)
from app.validation import NOT_READ_ONLY, ValidatedQuery

SCHEMA = "TABLE customers\n  id INTEGER PRIMARY KEY"


def run(reply, db_type="sqlite"):
    provider = FakeProvider([reply])
    return generate_validated_query("How many customers?", provider, SCHEMA, db_type), provider


def test_ready():
    o, _ = run("SELECT COUNT(*) FROM customers")
    assert o.status == READY
    assert isinstance(o.query, ValidatedQuery)
    assert o.query.sql.upper().startswith("SELECT")
    assert o.message is None and o.reason is None


def test_ready_shows_the_change_validation_made():
    o, _ = run("SELECT * FROM customers")
    assert o.status == READY and o.query.changes   # e.g. "Added LIMIT ..."


def test_ready_with_think_block_and_fences():
    o, _ = run("<think>count</think>\n```sql\nSELECT 1\n```")
    assert o.status == READY and "`" not in o.query.sql


def test_clarification():
    o, _ = run("CLARIFY: Which year?")
    assert (o.status, o.message, o.query) == (CLARIFICATION, "Which year?", None)


def test_not_found():
    o, _ = run("NOT_FOUND: No salary data.")
    assert (o.status, o.message, o.query) == (NOT_FOUND, "No salary data.", None)


def test_rejected_unsafe_sql_gets_reason_and_message():
    o, _ = run("SELECT 1; DROP TABLE customers")
    assert o.status == REJECTED and o.query is None
    assert o.reason and o.message


def test_rejected_write_hidden_in_cte():
    o, _ = run("WITH x AS (DELETE FROM customers RETURNING *) SELECT * FROM x", "postgresql")
    assert o.status == REJECTED and o.reason == NOT_READ_ONLY


def test_unreadable_reply_is_error():
    o, _ = run("Sure! Here you go.")
    assert o.status == ERROR and o.message and o.query is None


def test_model_failure_is_error():
    provider = FakeProvider([SetupProblem()])
    o = generate_validated_query("q", provider, SCHEMA, "sqlite")
    assert o.status == ERROR and o.message == SetupProblem().message


def test_provider_gets_the_right_prompt():
    _, provider = run("SELECT 1", "mysql")
    req = provider.requests[0]
    assert "mysql" in req.system_prompt and SCHEMA in req.user_prompt
