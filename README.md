# Text-to-SQL

Ask questions about your database in plain English. The app reads the schema, writes the SQL with an LLM (Gemini or Ollama), shows it to you, and runs it only after you approve.

**Status:** early build. Specs are written and the first backend pieces work. The app is not usable end to end yet.

## What works so far

Built in the order from `docs/reviews/spec-review.md` (section 7):

| Step | Piece | State |
|---|---|---|
| 1 | Project skeleton, Chinook test database, `/health` | Done |
| 2 | LLM providers: interface, retry rules, fake, **Ollama**, **Gemini** | Done |
| 3 | **Connection** (read-only lock, password hidden; SQLite tested, PostgreSQL/MySQL tested offline only) | Done |
| 4 | **Schema extraction** (tables, columns, keys, views to prompt text) | Done |
| 5 | **Query validation** (SELECT-only check with sqlglot, row limit) | Done |
| 6 | **Query execution** (runs the approved query with a time limit, makes values display-safe) | Done |
| 7 | Generate query | Not started |
| 8 | Pipeline and API | Not started |
| 9 | Benchmark | Not started |
| 10 | UI (`frontend/index.html` is only a stub) | Not started |
| 11-12 | PostgreSQL/MySQL, Ollama runs, write-up | Not started |

Schema extraction now reads from the real Connection (tested on the Chinook file).

## Folders
- `docs/` – intent, specs, reviews (start with `docs/README.md`)
- `backend/` – FastAPI app (Python)
  - `app/connection/` – opens the read-only database connection
  - `app/llm/` – Gemini and Ollama providers behind one interface
  - `app/execution/` – runs a validated query with a timeout and returns a table
  - `app/validation/` – checks the SQL is one safe SELECT and adds a row limit
  - `app/schema/` – reads the database structure into prompt text
  - `tests/` – unit tests (`tests/golden/` holds expected schema text)
- `frontend/` – plain HTML/JS page served by the backend
- `benchmark/` – accuracy test (fixtures, questions, results)

## Setup

```
python -m venv .venv
.venv\Scripts\activate          (Windows)   or   source .venv/bin/activate
pip install -r requirements.txt
python benchmark/fixtures/download_chinook.py
```

Then copy `.env.example` to `.env` and fill it in:
- **Gemini:** set `GEMINI_API_KEY`. Never commit `.env`.
- **Ollama:** install Ollama, run `ollama pull qwen2.5:7b`, keep it running.
- `LLM_PROVIDER` picks the default (`gemini` or `ollama`).

## Run the tests

```
pytest
```

The tests use fake models and small SQLite files, so they need no API key, no internet and no database server.

Tests marked `integration` need a real PostgreSQL or MySQL server and are skipped unless you set these variables (use a throwaway database; the tests never write to it):

```
TEST_PG_HOST  TEST_PG_PORT  TEST_PG_DB  TEST_PG_USER  TEST_PG_PASSWORD
TEST_MYSQL_HOST  TEST_MYSQL_PORT  TEST_MYSQL_DB  TEST_MYSQL_USER  TEST_MYSQL_PASSWORD
```

Windows PowerShell example: `$env:TEST_PG_HOST="localhost"; $env:TEST_PG_DB="chinook"; ...; pytest -m integration`

## Run the app

```
cd backend
uvicorn app.main:app --reload
```

Only `http://localhost:8000/health` does anything useful for now.
