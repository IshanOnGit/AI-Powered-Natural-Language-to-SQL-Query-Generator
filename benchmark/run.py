"""Accuracy benchmark (docs/specs/spec-benchmark.md).

    python -m benchmark.run --provider gemini --fixture all
    python -m benchmark.run --provider ollama --fixture chinook
    python -m benchmark.run --lint-only            (no model calls)
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.connection import ConnectionInput, connect          # noqa: E402
from app.execution import ExecutionError, execute             # noqa: E402
from app.llm import get_provider                              # noqa: E402
from app.llm.settings import load_settings as load_llm_settings   # noqa: E402
from app.pipeline import generate_validated_query             # noqa: E402
from app.schema import extract_schema                         # noqa: E402
from app.validation import validate                           # noqa: E402
from app.validation.settings import load_settings as load_query_settings   # noqa: E402
from benchmark.compare import results_match                   # noqa: E402

FIXTURES = ("chinook", "sakila")
FIXTURE_DIR = ROOT / "benchmark" / "fixtures"
QUESTION_DIR = ROOT / "benchmark" / "questions"
RESULT_DIR = ROOT / "benchmark" / "results"

CORRECT, WRONG_RESULT = "correct", "wrong_result"
EXECUTION_ERROR, REJECTED, MODEL_ERROR = "execution_error", "rejected", "model_error"
CLARIFICATION, NOT_FOUND = "clarification", "not_found"
GUESSED_SQL = "guessed_sql"      # ambiguity set only: the model wrote SQL instead of asking


# ---- loading and checking the questions ----

def load_questions(fixture: str):
    """Returns (answerable questions, ambiguity questions)."""
    items = yaml.safe_load((QUESTION_DIR / f"{fixture}.yaml").read_text(encoding="utf-8"))
    answerable = [q for q in items if "reference_sql" in q]
    ambiguity = [q for q in items if "expect" in q]
    return answerable, ambiguity


def open_fixture(fixture: str):
    path = FIXTURE_DIR / f"{fixture}.sqlite"
    if not path.is_file():
        raise SystemExit(f"Missing {path.name}. Run: python benchmark/fixtures/download_{fixture}.py")
    return connect(ConnectionInput("sqlite", database=str(path)))


def reference_rows(conn, question):
    """The expected answer is always computed by running the reference SQL."""
    check = validate(question["reference_sql"], "sqlite")
    if not check.ok:
        raise ValueError(f"{question['id']}: reference SQL rejected: {check.message}")
    return execute(conn, check.query).rows


def lint(conn, questions) -> list[str]:
    """Before any model call: each reference SQL runs and returns fewer rows
    than the default LIMIT, so the limit can't change the right answer."""
    limit = load_query_settings().default_limit
    problems = []
    for q in questions:
        try:
            rows = reference_rows(conn, q)
        except Exception as e:
            problems.append(f"{q['id']}: reference SQL failed ({type(e).__name__}: {e})")
            continue
        if not rows:
            problems.append(f"{q['id']}: reference SQL returns no rows")
        elif len(rows) >= limit:
            problems.append(f"{q['id']}: reference returns {len(rows)} rows (must be under {limit})")
    return problems


# ---- running one question ----

def run_question(q, conn, schema_text, provider, expected_rows=None) -> dict:
    started = time.perf_counter()
    record = {"id": q["id"], "question": q["question"], "generated_sql": None,
              "outcome": None, "row_count": None, "error": None}
    outcome = generate_validated_query(q["question"], provider, schema_text, "sqlite")

    if outcome.status == "ready":
        record["generated_sql"] = outcome.query.sql
        if "expect" in q:
            record["outcome"] = GUESSED_SQL          # not run: it should have asked instead
        else:
            _run_and_compare(record, q, conn, outcome.query, expected_rows)
    elif outcome.status == "rejected":
        record.update(outcome=REJECTED, error=outcome.message)
    elif outcome.status == "error":
        record.update(outcome=MODEL_ERROR, error=outcome.message)
    else:                                            # clarification / not_found
        record.update(outcome=outcome.status, error=outcome.message)

    record["seconds"] = round(time.perf_counter() - started, 2)
    return record


def _run_and_compare(record, q, conn, query, expected_rows):
    try:
        result = execute(conn, query)
    except ExecutionError as e:
        record.update(outcome=EXECUTION_ERROR, error=e.message)
        return
    record["row_count"] = result.row_count
    same = results_match(expected_rows, result.rows, bool(q.get("ordered")))
    record["outcome"] = CORRECT if same else WRONG_RESULT


class _Recording:
    """Wraps a provider to remember the exact model name from its replies."""

    def __init__(self, provider):
        self._provider = provider
        self.model = None

    def generate(self, request):
        response = self._provider.generate(request)
        self.model = response.model
        return response

    def is_available(self):
        return self._provider.is_available()


# ---- the report ----

def summarize(records, ambiguity_records):
    n = len(records)
    count = lambda outcome: sum(r["outcome"] == outcome for r in records)
    correct, wrong = count(CORRECT), count(WRONG_RESULT)
    return {
        "answerable": n, "correct": correct, "wrong_result": wrong,
        "other_failures": n - correct - wrong,
        "accuracy": round(100 * correct / n) if n else 0,
        "ambiguity_total": len(ambiguity_records),
        "ambiguity_passed": sum(r["outcome"] == r["expect"] for r in ambiguity_records),
    }


def markdown(header, per_fixture) -> str:
    who = f"{header['provider']} ({header['model']})"
    lines = [
        f"# Benchmark: {who}, {header['date']}", "",
        f"Temperature {header['temperature']}"
        + (f", num_ctx {header['num_ctx']}" if header["num_ctx"] else "") + ".", "",
        "| Provider (model) | Fixture | Correct | Wrong result | Other failures | Accuracy |",
        "|---|---|---|---|---|---|",
    ]
    for name, (records, ambiguity) in per_fixture.items():
        s = summarize(records, ambiguity)
        lines.append(f"| {who} | {name} | {s['correct']}/{s['answerable']} | {s['wrong_result']} "
                     f"| {s['other_failures']} | {s['accuracy']}% |")
    lines += ["", "Ambiguity set (not part of accuracy): the model should ask or say not found.", "",
              "| Fixture | Question | Expected | Got | Passed |", "|---|---|---|---|---|"]
    for name, (_, ambiguity) in per_fixture.items():
        for r in ambiguity:
            lines.append(f"| {name} | {r['question']} | {r['expect']} | {r['outcome']} "
                         f"| {'yes' if r['outcome'] == r['expect'] else 'no'} |")
    return "\n".join(lines) + "\n"


def save(header, per_fixture) -> Path:
    RESULT_DIR.mkdir(exist_ok=True)
    stem = f"{header['date'].replace(':', '').replace(' ', '_')}_{header['provider']}"
    detail = {"header": header, "fixtures": {
        name: {"summary": summarize(records, ambiguity),
               "answerable": records, "ambiguity": ambiguity}
        for name, (records, ambiguity) in per_fixture.items()}}
    (RESULT_DIR / f"{stem}.json").write_text(json.dumps(detail, indent=2), encoding="utf-8")
    md_path = RESULT_DIR / f"{stem}.md"
    md_path.write_text(markdown(header, per_fixture), encoding="utf-8")
    return md_path


# ---- command line ----

def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m benchmark.run")
    parser.add_argument("--provider", choices=("gemini", "ollama"))
    parser.add_argument("--fixture", choices=(*FIXTURES, "all"), default="all")
    parser.add_argument("--pause", type=float, default=None,
                        help="seconds between questions (default 4 for gemini, 0 for ollama)")
    parser.add_argument("--lint-only", action="store_true", help="check the questions, no model calls")
    args = parser.parse_args(argv)
    if not args.lint_only and not args.provider:
        parser.error("--provider is required (or use --lint-only)")

    names = FIXTURES if args.fixture == "all" else (args.fixture,)
    loaded = {}
    for name in names:                                  # lint everything first
        conn = open_fixture(name)
        answerable, ambiguity = load_questions(name)
        problems = lint(conn, answerable)
        if problems:
            raise SystemExit("Question check failed:\n  " + "\n  ".join(problems))
        print(f"{name}: {len(answerable)} questions + {len(ambiguity)} ambiguity questions OK")
        loaded[name] = (conn, answerable, ambiguity)
    if args.lint_only:
        return

    provider = _Recording(get_provider(args.provider))
    if not provider.is_available():
        raise SystemExit(f"The {args.provider} provider is not available. Check your .env settings.")
    pause = args.pause if args.pause is not None else (4.0 if args.provider == "gemini" else 0.0)

    per_fixture = {}
    for name, (conn, answerable, ambiguity) in loaded.items():
        schema_text = extract_schema(conn).render_text()
        records, ambiguity_records = [], []
        for q in answerable + ambiguity:
            expected = reference_rows(conn, q) if "reference_sql" in q else None
            record = run_question(q, conn, schema_text, provider, expected)
            print(f"  {q['id']}: {record['outcome']}")
            if "expect" in q:
                record["expect"] = q["expect"]
                ambiguity_records.append(record)
            else:
                records.append(record)
            time.sleep(pause)
        per_fixture[name] = (records, ambiguity_records)

    settings = load_llm_settings()
    header = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"), "provider": args.provider,
        "model": provider.model or "unknown", "temperature": settings.temperature,
        "num_ctx": settings.ollama_num_ctx if args.provider == "ollama" else None,
        "fixtures": list(names),
    }
    path = save(header, per_fixture)
    print("\n" + markdown(header, per_fixture))
    print(f"Saved: {path} (and the .json detail file)")


if __name__ == "__main__":
    main()
