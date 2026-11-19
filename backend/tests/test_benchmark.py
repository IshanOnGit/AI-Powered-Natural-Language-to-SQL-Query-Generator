import json

import pytest

from app.connection import ConnectionInput, connect
from app.llm import SetupProblem
from app.llm.fake import FakeProvider
from app.schema import extract_schema
from benchmark import run
from benchmark.compare import results_match

CHINOOK = run.FIXTURE_DIR / "chinook.sqlite"


# ---- comparison rules (spec section 5) ----

def test_same_rows_match():
    assert results_match([["a", 1], ["b", 2]], [["a", 1], ["b", 2]])


def test_reordered_rows_ok_unless_ordered():
    assert results_match([["a", 1], ["b", 2]], [["b", 2], ["a", 1]])
    assert not results_match([["a", 1], ["b", 2]], [["b", 2], ["a", 1]], ordered=True)


def test_duplicates_are_counted():
    assert not results_match([["a"], ["a"]], [["a"], ["b"]])
    assert not results_match([["a"], ["a"], ["b"]], [["a"], ["b"], ["b"]])


def test_extra_or_missing_column_is_wrong():
    assert not results_match([["a", 1]], [["a", 1, "x"]])
    assert not results_match([["a", 1]], [["a"]])


def test_wrong_row_count_is_wrong():
    assert not results_match([["a"]], [["a"], ["b"]])
    assert not results_match([["a"], ["b"]], [["a"]])


def test_numbers_use_a_tiny_tolerance_and_numeric_text_counts():
    assert results_match([[12.5]], [["12.50"]])
    assert results_match([["12.50"]], [[12.5]])
    assert results_match([[1]], [[1.0000000001]])
    assert not results_match([[1]], [[1.01]])


def test_text_is_exact():
    assert not results_match([["Rock"]], [["rock"]])
    assert not results_match([["Rock"]], [["Rock "]])


def test_none_only_matches_none():
    assert results_match([[None]], [[None]])
    assert not results_match([[None]], [[0]])
    assert not results_match([[None]], [[""]])


def test_empty_results_match():
    assert results_match([], [])


# ---- the questions themselves ----

@pytest.mark.parametrize("fixture", run.FIXTURES)
def test_question_files_are_well_formed(fixture):
    answerable, ambiguity = run.load_questions(fixture)
    assert len(answerable) == 5 and len(ambiguity) == 2
    ids = [q["id"] for q in answerable + ambiguity]
    assert len(ids) == len(set(ids))
    assert all(q["fixture"] == fixture for q in answerable + ambiguity)
    assert {q["expect"] for q in ambiguity} == {"clarification", "not_found"}


def test_chinook_reference_queries_run_and_are_small():
    conn = run.open_fixture("chinook")
    answerable, _ = run.load_questions("chinook")
    assert run.lint(conn, answerable) == []


def test_lint_catches_bad_reference_sql():
    conn = run.open_fixture("chinook")
    bad = [
        {"id": "x1", "reference_sql": "SELECT * FROM NoSuchTable"},
        {"id": "x2", "reference_sql": "SELECT * FROM Customer WHERE 1 = 0"},
        {"id": "x3", "reference_sql": "SELECT * FROM Track"},          # 3503 rows
        {"id": "x4", "reference_sql": "DELETE FROM Customer"},
    ]
    problems = run.lint(conn, bad)
    assert len(problems) == 4


# ---- each outcome lands in the right place ----

@pytest.fixture(scope="module")
def chinook():
    conn = connect(ConnectionInput("sqlite", database=str(CHINOOK)))
    return conn, extract_schema(conn).render_text()


Q = {"id": "t1", "question": "How many genres?", "ordered": False}
COUNT_GENRES = "SELECT COUNT(*) FROM Genre"


def outcome_for(chinook, reply, q=None):
    conn, schema = chinook
    q = q or Q
    expected = None
    if "reference_sql" in q:
        expected = run.reference_rows(conn, q)
    return run.run_question(q, conn, schema, FakeProvider([reply]), expected)


def with_ref(**extra):
    return {**Q, "reference_sql": COUNT_GENRES, **extra}


def test_correct(chinook):
    r = outcome_for(chinook, "SELECT COUNT(*) AS n FROM Genre", with_ref())   # alias is fine
    assert r["outcome"] == run.CORRECT and r["row_count"] == 1
    assert r["generated_sql"].upper().startswith("SELECT")


def test_wrong_result_runs_but_differs(chinook):
    r = outcome_for(chinook, "SELECT COUNT(*) FROM Artist", with_ref())
    assert r["outcome"] == run.WRONG_RESULT


def test_extra_column_is_wrong_result(chinook):
    r = outcome_for(chinook, "SELECT COUNT(*), 1 FROM Genre", with_ref())
    assert r["outcome"] == run.WRONG_RESULT


def test_execution_error(chinook):
    r = outcome_for(chinook, "SELECT COUNT(*) FROM NoSuchTable", with_ref())
    assert r["outcome"] == run.EXECUTION_ERROR and r["error"]


def test_rejected(chinook):
    r = outcome_for(chinook, "SELECT 1; DROP TABLE Genre", with_ref())
    assert r["outcome"] == run.REJECTED and r["generated_sql"] is None


def test_clarification_and_not_found(chinook):
    assert outcome_for(chinook, "CLARIFY: Which?", with_ref())["outcome"] == run.CLARIFICATION
    assert outcome_for(chinook, "NOT_FOUND: no", with_ref())["outcome"] == run.NOT_FOUND


def test_garbage_is_model_error(chinook):
    assert outcome_for(chinook, "I like turtles", with_ref())["outcome"] == run.MODEL_ERROR
    assert outcome_for(chinook, SetupProblem(), with_ref())["outcome"] == run.MODEL_ERROR


def test_ordered_question_checks_order(chinook):
    q = with_ref(reference_sql="SELECT Name FROM Genre ORDER BY GenreId LIMIT 3", ordered=True)
    good = outcome_for(chinook, "SELECT Name FROM Genre ORDER BY GenreId LIMIT 3", q)
    backwards = outcome_for(chinook, "SELECT Name FROM Genre ORDER BY GenreId DESC LIMIT 3", q)
    assert good["outcome"] == run.CORRECT
    assert backwards["outcome"] == run.WRONG_RESULT


def test_ambiguity_question_that_gets_sql_counts_as_guessed(chinook):
    q = {"id": "a", "question": "Top customers?", "expect": "clarification"}
    assert outcome_for(chinook, "SELECT * FROM Customer", q)["outcome"] == run.GUESSED_SQL
    assert outcome_for(chinook, "CLARIFY: By what?", q)["outcome"] == run.CLARIFICATION


# ---- summary and the whole command ----

def test_summary_numbers():
    records = [{"outcome": o} for o in
               [run.CORRECT, run.CORRECT, run.WRONG_RESULT, run.REJECTED, run.MODEL_ERROR]]
    amb = [{"outcome": "clarification", "expect": "clarification"},
           {"outcome": run.GUESSED_SQL, "expect": "not_found"}]
    s = run.summarize(records, amb)
    assert (s["correct"], s["wrong_result"], s["other_failures"], s["accuracy"]) == (2, 1, 2, 40)
    assert (s["ambiguity_total"], s["ambiguity_passed"]) == (2, 1)


def test_whole_run_with_fake_provider_writes_both_files(tmp_path, monkeypatch):
    replies = {  # one reply per question, in the order the benchmark asks them
        "Brazil": "SELECT FirstName, LastName, Email FROM Customer WHERE Country = 'Brazil'",
        "Led Zeppelin": "SELECT 1",
        "each country": "SELECT Country, COUNT(*) FROM Customer GROUP BY Country",
    }

    class Smart(FakeProvider):
        def _generate_once(self, request):
            for key, reply in replies.items():
                if key in request.user_prompt:
                    self._replies = [reply]
                    break
            else:
                self._replies = ["CLARIFY: Can you be more specific?"]
            self.requests.clear()
            return super()._generate_once(request)

    monkeypatch.setattr(run, "get_provider", lambda name: Smart())
    monkeypatch.setattr(run, "RESULT_DIR", tmp_path)
    run.main(["--provider", "ollama", "--fixture", "chinook", "--pause", "0"])

    files = sorted(p.name for p in tmp_path.iterdir())
    assert len(files) == 2 and files[0].endswith(".json") and files[1].endswith(".md")
    data = json.loads((tmp_path / files[0]).read_text())
    assert data["header"]["model"] == "fake-model" and data["header"]["provider"] == "ollama"
    outcomes = {r["id"]: r["outcome"] for r in data["fixtures"]["chinook"]["answerable"]}
    assert outcomes["chinook-01"] == run.CORRECT
    assert outcomes["chinook-02"] == run.WRONG_RESULT
    assert outcomes["chinook-03"] == run.CORRECT
    assert outcomes["chinook-04"] == run.CLARIFICATION
    amb = {r["id"]: r["outcome"] for r in data["fixtures"]["chinook"]["ambiguity"]}
    assert amb == {"chinook-A1": "clarification", "chinook-A2": "clarification"}
    md = (tmp_path / files[1]).read_text()
    assert "| Provider (model) | Fixture |" in md and "fake-model" in md


def test_missing_fixture_gives_a_clear_message():
    if (run.FIXTURE_DIR / "sakila.sqlite").exists():
        pytest.skip("Sakila is installed")
    with pytest.raises(SystemExit) as e:
        run.open_fixture("sakila")
    assert "download_sakila.py" in str(e.value)
