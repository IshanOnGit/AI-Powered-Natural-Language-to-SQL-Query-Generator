# AI Powered Natural Language to SQL Query Generator : Text-to-SQL

Text-to-SQL is a complete, local-first web application that converts natural-language questions into safe SQL queries. It connects to PostgreSQL, MySQL, or SQLite, reads the database schema automatically, generates SQL with Gemini or a local Ollama model, validates it, shows it for approval, and only then executes it.

**Project status:** The FastAPI backend, plain HTML/CSS/JavaScript frontend, safety pipeline, and benchmark runner are all implemented. Provider accuracy benchmarks can be run with configured Gemini or Ollama services.

## Demo Flow

1. Connect to PostgreSQL, MySQL, or a SQLite file.
2. Choose Gemini or an available Ollama model.
3. Ask a question in plain English.
4. Review the generated SQL and any changes made by the validator.
5. Approve the query to run it.
6. Read the returned data in a table.

Ambiguous or unknown questions receive a clarification or not-found response instead of silently guessing.

## Features

- PostgreSQL, MySQL, and SQLite connections
- Automatic schema extraction for tables, columns, primary keys, foreign keys, and views
- Schema-aware SQL generation
- Gemini and Ollama providers with a shared provider interface
- Clarification and not-found outcomes for ambiguous or invalid questions
- SELECT-only SQL validation with SQLGlot
- Automatic row limits (100 by default, 1000 maximum)
- Read-only database connections
- Explicit query approval with a server-side one-time query ID
- Query execution with timeout and display-safe result values
- Plain, responsive one-page frontend
- Command-line benchmark with SQLite Chinook and Sakila fixtures
- Friendly API errors without leaking credentials or stack traces

## Safety Model

The app uses multiple independent safety boundaries:

- The browser never sends SQL to the server and cannot edit the generated SQL.
- Generated SQL must pass the validator as a single read-only SELECT statement.
- SQLite connections use read-only file mode.
- PostgreSQL transactions use read-only mode; MySQL sessions use read-only transactions.
- `/execute` accepts only a server-created query ID, not SQL text.
- A pending query is single-use and cleared after execution or replacement.
- Database passwords remain in memory only and are never returned or logged.
- Generated SQL execution uses limits and database-side timeouts where supported.

See [the API spec](docs/specs/spec-api.md) and [the query validation spec](docs/specs/spec-query-validation.md) for the complete design.

## Architecture

```text
Browser
  -> Connection
  -> Schema extraction
  -> Prompt and LLM generation
  -> SQL validation
  -> Server-side approval ID
  -> Read-only query execution
  -> Table result
```

```text
backend/app/
  connection/   database connections and credential handling
  schema/       schema extraction and prompt formatting
  llm/          provider interface, Gemini, Ollama, and fake provider
  generate/     prompt construction and model reply parsing
  validation/   SQLGlot validation and row limits
  execution/    timeout-protected execution and value formatting
  pipeline/     question-to-validated-SQL orchestration
  api/          FastAPI routes and approval flow
  main.py       application and static frontend setup

frontend/       plain HTML, CSS, and JavaScript
benchmark/      fixtures, questions, runner, comparison, and results
docs/           intent, specifications, and build review
backend/tests/  mirrored unit and integration tests
```

## Requirements

- Python 3.10 or newer
- PostgreSQL or MySQL only when testing those databases
- Gemini: a Gemini API key
- Ollama: an Ollama installation and a pulled model
- SQLite: no separate database server

## Setup

Create and activate a virtual environment:

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

Create a `.env` file in the project root. It is ignored by Git and is never logged by the app.

```dotenv
LLM_PROVIDER=gemini
LLM_TEMPERATURE=0
LLM_TIMEOUT_SECONDS=30

GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-2.5-flash

OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_NUM_CTX=8192
```

To use Ollama, install and start it, then pull the configured model:

```powershell
ollama pull qwen2.5:7b
ollama serve
```

Download the Chinook SQLite fixture:

```powershell
python benchmark/fixtures/download_chinook.py
```

Sakila is downloaded with:

```powershell
python benchmark/fixtures/download_sakila.py
```

## Run the Application

From the project root:

```powershell
cd backend
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000).

Useful endpoints:

| Endpoint | Purpose |
|---|---|
| `GET /health` | Basic application health check |
| `GET /status` | Connection state and available providers |
| `POST /connect` | Connect and extract the schema |
| `POST /refresh-schema` | Re-read the active database schema |
| `POST /disconnect` | Close the connection and clear pending state |
| `POST /generate` | Generate and validate SQL for a question |
| `POST /execute` | Execute one approved query ID |

The frontend never sends SQL or database passwords in a query-preview or execute request.

## Running Tests

Install the development dependencies first:

```powershell
python -m pip install -r requirements.txt
python -m pytest
```

The normal suite uses the fake provider and local SQLite fixtures, so it does not require API keys or database servers. Real PostgreSQL/MySQL tests are integration tests and are skipped unless their `TEST_*` variables are set.

```dotenv
TEST_PG_HOST=localhost
TEST_PG_PORT=5432
TEST_PG_DB=chinook
TEST_PG_USER=your_user
TEST_PG_PASSWORD=your_password

TEST_MYSQL_HOST=localhost
TEST_MYSQL_PORT=3306
TEST_MYSQL_DB=chinook
TEST_MYSQL_USER=your_user
TEST_MYSQL_PASSWORD=your_password
```

PowerShell example:

```powershell
$env:TEST_PG_HOST="localhost"
$env:TEST_PG_DB="chinook"
$env:TEST_PG_USER="postgres"
$env:TEST_PG_PASSWORD="password"
python -m pytest -m integration
```

## Benchmarks

The benchmark runner compares generated result tables with reference answers rather than comparing SQL text.

Lint all answerable and ambiguity questions without calling a model:

```powershell
python -m benchmark.run --lint-only
```

Run the SQLite benchmark with Gemini:

```powershell
python -m benchmark.run --provider gemini --fixture all
```

Run one fixture with local Ollama:

```powershell
python -m benchmark.run --provider ollama --fixture chinook
```

The benchmark outputs JSON details and a Markdown summary in [benchmark/results/](benchmark/results/). It reports:

- Correct result-table matches
- Wrong-but-running answers
- Rejected, execution-error, and model-error outcomes
- Clarification and not-found behavior
- Exact provider model information

The benchmark suite contains 5 Chinook questions, 5 Sakila questions, and separate ambiguity questions for both fixtures. Committed provider accuracy results are not present until real Gemini and Ollama benchmark runs are performed and saved.

## Documentation

- [Project intent](docs/intent.md)
- [Documentation index](docs/README.md)
- [Specifications](docs/specs/)
- [Cross-spec review and build order](docs/reviews/spec-review.md)

## Scope

V1 is intentionally a single-user, local demo. Authentication, multi-user connections, query history, result export, conversational follow-up, schema caching, and public hosting are not included.
