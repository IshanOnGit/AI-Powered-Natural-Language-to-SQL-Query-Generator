import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.llm import RateLimited, SetupProblem
from app.llm.fake import FakeProvider
from app.main import app

CHINOOK = Path(__file__).parents[2] / "benchmark" / "fixtures" / "chinook.sqlite"
SQLITE = {"db_type": "sqlite", "database": str(CHINOOK)}
CLIENT = TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def api(monkeypatch):
    """Clean session each test; get_provider returns a FakeProvider we control."""
    routes.session.clear()
    chosen = []
    state = {"replies": ["SELECT COUNT(*) FROM Genre"]}

    def fake_get_provider(name=None):
        chosen.append(name)
        return FakeProvider(state["replies"])

    monkeypatch.setattr(routes, "get_provider", fake_get_provider)
    CLIENT.chosen = chosen
    CLIENT.state = state
    yield CLIENT
    routes.session.clear()


def connected(api):
    assert api.post("/connect", json=SQLITE).status_code == 200


def generate(api, reply, **extra):
    api.state["replies"] = [reply]
    return api.post("/generate", json={"question": "how many genres?", **extra})


def assert_error(r, status, code):
    assert r.status_code == status
    assert set(r.json()) == {"code", "message"} and r.json()["code"] == code


# ---- connect / status / disconnect ----

def test_connect_returns_summary_and_status_shows_it(api):
    r = api.post("/connect", json=SQLITE)
    assert r.status_code == 200
    body = r.json()
    assert body["db_type"] == "sqlite" and body["table_count"] > 5
    assert isinstance(body["warnings"], list)
    s = api.get("/status").json()
    assert s["connected"] and s["db_type"] == "sqlite" and s["table_count"] == body["table_count"]


def test_status_before_connect(api):
    s = api.get("/status").json()
    assert s["connected"] is False and s["table_count"] == 0


def test_providers_available_comes_from_is_available(api, monkeypatch):
    monkeypatch.setattr(routes, "get_provider",
                        lambda name=None: FakeProvider(available=(name == "ollama")))
    assert api.get("/status").json()["providers_available"] == ["ollama"]


def test_provider_that_cannot_be_set_up_is_not_offered(api, monkeypatch):
    def broken(name=None):
        raise SetupProblem()
    monkeypatch.setattr(routes, "get_provider", broken)
    assert api.get("/status").json()["providers_available"] == []


def test_connect_failure_leaves_nothing_connected(api):
    r = api.post("/connect", json={"db_type": "sqlite", "database": "no/such/file.db"})
    assert_error(r, 400, "db_not_found")
    assert api.get("/status").json()["connected"] is False


def test_connect_missing_database_for_sqlite(api):
    assert_error(api.post("/connect", json={"db_type": "sqlite"}), 400, "db_invalid_input")


def test_refresh_schema(api):
    connected(api)
    r = api.post("/refresh-schema")
    assert r.status_code == 200 and r.json()["table_count"] > 5


def test_refresh_schema_clears_pending_query(api):
    connected(api)
    qid = generate(api, "SELECT 1").json()["query_id"]
    api.post("/refresh-schema")
    assert_error(api.post("/execute", json={"query_id": qid}), 404, "query_unavailable")


def test_disconnect_clears_everything(api):
    connected(api)
    qid = generate(api, "SELECT 1").json()["query_id"]
    conn = routes.session.connection
    assert api.post("/disconnect").status_code == 200
    s = routes.session
    assert (s.connection, s.schema_text, s.pending) == (None, None, None)
    assert conn.closed
    assert api.get("/status").json()["connected"] is False
    assert_error(api.post("/execute", json={"query_id": qid}), 404, "query_unavailable")


def test_connecting_again_replaces_old_connection_and_pending(api):
    connected(api)
    qid = generate(api, "SELECT 1").json()["query_id"]
    first = routes.session.connection
    connected(api)
    assert first.closed
    assert_error(api.post("/execute", json={"query_id": qid}), 404, "query_unavailable")


# ---- order ----

def test_generate_before_connect(api):
    r = api.post("/generate", json={"question": "hi"})
    assert_error(r, 409, "not_connected")


def test_refresh_before_connect(api):
    assert_error(api.post("/refresh-schema"), 409, "not_connected")


# ---- the five outcomes of /generate ----

def test_generate_ready(api):
    connected(api)
    r = generate(api, "SELECT GenreId, Name FROM Genre")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "ready"
    assert body["query_id"] and body["sql"].upper().startswith("SELECT")
    assert isinstance(body["changes"], list)


def test_generate_clarification(api):
    connected(api)
    r = generate(api, "CLARIFY: Which year?")
    assert r.status_code == 200
    assert r.json() == {"status": "clarification", "message": "Which year?"}


def test_generate_not_found(api):
    connected(api)
    r = generate(api, "NOT_FOUND: No salary data.")
    assert r.json() == {"status": "not_found", "message": "No salary data."}


def test_generate_rejected(api):
    connected(api)
    r = generate(api, "SELECT 1; DROP TABLE Genre")
    body = r.json()
    assert r.status_code == 200 and body["status"] == "rejected"
    assert body["reason"] and body["message"] and "query_id" not in body


def test_generate_error_from_unreadable_reply(api):
    connected(api)
    r = generate(api, "Sure! Here you go.")
    assert r.status_code == 200 and r.json()["status"] == "error" and r.json()["message"]


def test_generate_error_from_model_failure(api):
    connected(api)
    api.state["replies"] = [RateLimited()]
    r = api.post("/generate", json={"question": "q"})
    assert r.status_code == 200 and r.json()["status"] == "error"


def test_generate_blank_question(api):
    connected(api)
    assert_error(api.post("/generate", json={"question": "   "}), 400, "bad_input")


def test_generate_unknown_provider_error_shape(api, monkeypatch):
    connected(api)

    def bad(name=None):
        raise SetupProblem("Unknown model provider: x")
    monkeypatch.setattr(routes, "get_provider", bad)
    assert_error(api.post("/generate", json={"question": "q", "provider": "x"}),
                 400, "llm_setup_problem")


def test_provider_choice_is_passed_on(api):
    connected(api)
    generate(api, "SELECT 1", provider="ollama")
    generate(api, "SELECT 1")
    assert api.chosen == ["ollama", None]


# ---- approval ----

def test_execute_runs_the_approved_query_once(api):
    connected(api)
    qid = generate(api, "SELECT GenreId, Name FROM Genre ORDER BY GenreId").json()["query_id"]
    r = api.post("/execute", json={"query_id": qid})
    body = r.json()
    assert r.status_code == 200
    assert body["columns"] == ["GenreId", "Name"]
    assert body["row_count"] == len(body["rows"]) > 0
    assert "elapsed_ms" in body
    assert_error(api.post("/execute", json={"query_id": qid}), 404, "query_unavailable")


def test_execute_with_wrong_id(api):
    connected(api)
    generate(api, "SELECT 1")
    assert_error(api.post("/execute", json={"query_id": "nope"}), 404, "query_unavailable")


def test_wrong_id_does_not_use_up_the_real_one(api):
    connected(api)
    qid = generate(api, "SELECT 1").json()["query_id"]
    api.post("/execute", json={"query_id": "nope"})
    assert api.post("/execute", json={"query_id": qid}).status_code == 200


def test_execute_with_nothing_pending(api):
    connected(api)
    assert_error(api.post("/execute", json={"query_id": "x"}), 404, "query_unavailable")


def test_new_generate_replaces_pending_query(api):
    connected(api)
    old = generate(api, "SELECT 1").json()["query_id"]
    new = generate(api, "SELECT 2").json()["query_id"]
    assert old != new
    assert_error(api.post("/execute", json={"query_id": old}), 404, "query_unavailable")
    assert api.post("/execute", json={"query_id": new}).status_code == 200


def test_generate_without_sql_result_also_clears_pending(api):
    connected(api)
    old = generate(api, "SELECT 1").json()["query_id"]
    generate(api, "CLARIFY: Which?")
    assert_error(api.post("/execute", json={"query_id": old}), 404, "query_unavailable")


def test_execute_ignores_any_sql_sent_by_the_browser(api):
    connected(api)
    qid = generate(api, "SELECT 111 AS approved").json()["query_id"]
    r = api.post("/execute", json={"query_id": qid, "sql": "SELECT 222 AS sneaky"})
    assert r.json()["columns"] == ["approved"]


def test_execute_without_query_id_is_bad_input(api):
    connected(api)
    assert_error(api.post("/execute", json={"sql": "SELECT 1"}), 400, "bad_input")


def test_failed_query_still_uses_up_the_id(api):
    connected(api)
    qid = generate(api, "SELECT * FROM no_such_table").json()["query_id"]
    assert_error(api.post("/execute", json={"query_id": qid}), 400, "query_failed")
    assert_error(api.post("/execute", json={"query_id": qid}), 404, "query_unavailable")


# ---- errors and credentials ----

def test_connect_missing_field_does_not_echo_password(api, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "hunter2-very-secret"
    r = api.post("/connect", json={"host": "h", "username": "u", "password": secret})  # no db_type
    assert_error(r, 400, "bad_input")
    assert "db_type" in r.json()["message"]
    assert secret not in r.text
    assert secret not in caplog.text


def test_wrong_type_does_not_echo_password(api, caplog):
    caplog.set_level(logging.DEBUG)
    secret = "hunter2-very-secret"
    r = api.post("/connect", json={"db_type": "postgresql", "port": "abc", "password": secret})
    assert_error(r, 400, "bad_input")
    assert secret not in r.text and secret not in caplog.text


def test_questions_and_sql_are_not_logged(api, caplog):
    caplog.set_level(logging.DEBUG)
    connected(api)
    api.post("/generate", json={"question": "my-private-question-text"})
    generate(api, "SELECT 'my-private-sql-text'")
    assert "my-private" not in caplog.text


def test_unexpected_error_is_internal_with_no_details(api, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret-password-in-here")
    monkeypatch.setattr(routes, "extract_schema", boom)
    r = api.post("/connect", json=SQLITE)
    assert_error(r, 500, "internal")
    assert "secret-password" not in r.text
    assert api.get("/status").json()["connected"] is False   # nothing half-connected


def test_error_codes_have_matching_prefixes(api):
    connected(api)
    # connection (db_), model (llm_), query (query_) each reach the client as {code, message}
    assert api.post("/connect", json={"db_type": "sqlite", "database": "x.db"}).json()["code"].startswith("db_")
    connected(api)
    api.state["replies"] = ["SELECT * FROM no_such_table"]
    qid = api.post("/generate", json={"question": "q"}).json()["query_id"]
    assert api.post("/execute", json={"query_id": qid}).json()["code"].startswith("query_")


def test_unknown_route_has_the_same_error_shape(api):
    assert_error(api.get("/nope"), 404, "http_error")


def test_http_status_by_error_type():
    from app.api.errors import status_for
    from app.connection import AuthFailed, Unreachable as DbUnreachable
    from app.execution import Timeout as QueryTimeout
    from app.llm import Timeout as LlmTimeout, Unreachable as LlmUnreachable
    assert status_for(AuthFailed()) == 400
    assert status_for(DbUnreachable()) == 502
    assert status_for(LlmUnreachable()) == 502
    assert status_for(RateLimited()) == 502
    assert status_for(LlmTimeout()) == 504
    assert status_for(QueryTimeout()) == 504
