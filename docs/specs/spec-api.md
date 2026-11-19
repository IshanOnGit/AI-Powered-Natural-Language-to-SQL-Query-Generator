# Spec: API and Approval Flow

**Status:** Draft 1.1
**Depends on:** `spec-connection.md`, `spec-schema-extraction.md`, `spec-generate-query.md`, `spec-query-validation.md`, `spec-query-execution.md`, `spec-llm-provider-interface.md`
**Used by:** UI (`spec-ui.md`)

---

## 1. Purpose

The API is the thin layer between the web page and the components. It owns:

- The list of endpoints the UI calls
- The **approval mechanism** — how "nothing runs without approval" is actually enforced
- Turning component errors into one clean response shape
- Holding the session state (the one active connection, its schema text, one pending query)

It contains **no SQL or prompt logic**. It only calls the other components in order.

Stack: Python with FastAPI. The page is plain HTML/JS served by the same backend (see `spec-ui.md`), so there is one thing to run.

---

## 2. Session State (in memory only)

V1 is single-user and local, so the backend holds one global state:

```python
@dataclass
class SessionState:
    connection: Connection | None = None
    schema_text: str | None = None
    schema_warnings: list[str] = field(default_factory=list)
    pending: tuple[str, ValidatedQuery] | None = None    # (query_id, query): at most ONE
```

- Nothing is written to disk. Restarting the backend clears everything, including credentials.
- The server listens on **127.0.0.1 only**. There is no login in V1 (multi-user and auth are Phase 3).

---

## 3. Endpoints

| Method and path | Body | Does |
|---|---|---|
| `POST /connect` | `ConnectionInput` fields | Connect, extract schema, store both. Returns `{db_type, table_count, warnings}` |
| `POST /refresh-schema` | none | Re-extract the schema |
| `POST /disconnect` | none | Close connection, clear credentials, schema and pending query |
| `POST /generate` | `{question, provider?}` | Run the pipeline (section 4). Returns an outcome (section 5) |
| `POST /execute` | `{query_id}` | Run the approved query. Returns the table result |
| `GET /status` | none | `{connected, db_type, table_count, providers_available}` |

- `provider` is `"gemini"` or `"ollama"`; if omitted, the `LLM_PROVIDER` default is used. The API calls `get_provider(name)` from the provider spec.
- `providers_available` comes from each provider's `is_available()`, so the page only offers providers that can work.

---

## 4. The Pipeline

One plain function does the whole "question to checked SQL" job. The `/generate` endpoint and the benchmark both call it. It takes plain values, **not** the session object, so the benchmark can use it without any web code.

```python
def generate_validated_query(question, provider, schema_text, db_type) -> PipelineOutcome:
    result = generate_query(GenerateQueryInput(question, schema_text, db_type), provider)
    if result.status != "sql":
        return outcome_from(result)           # clarification / not_found / error
    check = validate(result.sql, db_type)
    return ready_or_rejected(check)           # ValidatedQuery or a rejection
```

The API's `/generate` does the surrounding steps: check it is connected, call `get_provider(name)`, call this function, and if the outcome is `ready`, store the query in `session.pending` under a new random `query_id`.

---

## 5. Outcomes of `/generate`

An expected outcome is a normal `200` response with a `status`. Only real failures use error codes (section 7).

| `status` | Meaning | Other fields |
|---|---|---|
| `ready` | SQL passed validation, awaiting approval | `query_id`, `sql`, `changes` |
| `clarification` | Question is ambiguous | `message` |
| `not_found` | Something asked about isn't in the database | `message` |
| `rejected` | The SQL was unsafe or invalid | `message`, `reason` (the validation reason code) |
| `error` | Reply unreadable or model failed | `message` |

---

## 6. The Approval Mechanism

This is the core safety rule from `intent.md`: **nothing runs without approval**, and **what is approved is what runs.**

- `/generate` returns the final SQL **and** a `query_id`. The SQL itself is stored on the server in the one `pending` slot.
- `/execute` takes **only the `query_id`**. It never accepts SQL text from the browser.
- `query_id` must match the one in `pending`. If it doesn't, the answer is "This query is no longer available. Please generate it again."
- Running it **clears** `pending`, so it can only run once.
- A new `/generate`, `/refresh-schema` or `/disconnect` also clears `pending`.

So the page cannot change the SQL after validation, cannot run something that was never validated, and an old browser tab cannot run a stale query.

Pressing "Approve & run" in the UI is the only thing that calls `/execute`. Nothing in the pipeline calls it automatically.

---

## 7. Errors

### 7.1 One shared error base

All components raise errors built on one small base class, so the API handles them in one place:

```python
class AppError(Exception):
    code: str        # e.g. "llm_timeout", "db_auth_failed", "query_timeout"
    message: str     # friendly text, safe to show
```

Each component's own base (`LLMError`, `DBConnectionError`, `SchemaError`, `ExecutionError`) subclasses `AppError` and prefixes its codes (`llm_`, `db_`, `schema_`, `query_`). This avoids four different `Timeout` classes that mean different things.

### 7.2 Responses

| Situation | HTTP | Body |
|---|---|---|
| Expected outcome (clarification, not found, rejected) | 200 | `status` as in section 5 |
| Not connected | 409 | `{code: "not_connected", message}` |
| Bad input (missing field) | 400 | `{code, message}` |
| Any `AppError` | 400, 502 or 504 by type | `{code, message}` from the error |
| Unknown or used `query_id` | 404 | `{code: "query_unavailable", message}` |
| Anything unexpected | 500 | `{code: "internal", message: "Something went wrong."}` |

All error bodies have the same shape: `{code, message}`.

### 7.3 Credentials must not leak through the framework

FastAPI's default validation error echoes the submitted input back, which would include the password. So:
- Replace the default validation-error handler with one that returns only the field name and a fixed message.
- Never log request bodies.
- Do not enable debug tracebacks in responses.

---

## 8. Logging

- Log only: endpoint, status, timing, error `code`.
- **Never log:** passwords, request bodies, the API key, or the user's question text and SQL (query history is Phase 2 audit logging).

---

## 9. Testing

- Run the app with a test client against SQLite Chinook and the `FakeProvider` (no real model).
- **Approval:** `/execute` with a wrong ID fails; with the right ID works once and fails the second time; a new `/generate` replaces the pending query so the old ID fails; there is no way to pass SQL to `/execute`.
- **Pipeline:** for each outcome (`ready`, `clarification`, `not_found`, `rejected`, `error`), a fake model reply gives the matching response.
- **Order:** `/generate` before `/connect` gives `not_connected`.
- **Credentials:** send a connect request with a missing field, and check the password is not in the error response body or in captured logs.
- **Disconnect** clears pending query and credentials.
- **Provider choice:** `provider: "ollama"` selects the Ollama provider even if the default says Gemini.
- **Error shape:** a forced error from each component comes back as `{code, message}` with the right prefix.

---

## 10. Deferred (not in V1)

- Authentication, multiple users, one connection per user (Phase 3)
- Query history and audit log (Phase 2)
- Streaming the model's reply
- Rate limiting and restricting which hosts can be connected to (needed for public deployment; see the deployment phase in `intent.md`)
- An endpoint that returns the schema (the UI does not show it)

---

## 11. Interview Note

**What this taught me:** the safest way to enforce "approve before run" is to make it impossible to skip, not just to hide the button. The server keeps the checked SQL, and the run endpoint accepts only an ID.

**What I'd say in an interview:** "The browser never sends SQL back to be executed. It gets an ID for a query the server has already validated, and the run endpoint only accepts that ID, once. So even a modified frontend couldn't run something that skipped validation. I also noticed the web framework's default error responses can echo a submitted password, and replaced that handler."

---

## 12. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: session state, endpoints, pipeline, approval via query_id, error shape, logging, testing |
| Draft 1.1 | One pending-query slot instead of a dict with expiry; shared `AppError` base; pipeline takes plain values; provider choice via `get_provider` / `is_available`; removed `WriteBlocked` logging |
| Draft 1.2 | Built in `backend/app/api/` and `backend/app/pipeline/`. Added `table_count` to session state (for `/status`). Bad input gives `bad_input`; unknown routes give `http_error`. 409 = `not_connected`, 404 = `query_unavailable`. HTTP status by code: `*timeout` 504; `*unreachable`, `*connection_lost`, `*rate_limited` 502; other app errors 400. A failed `/execute` still uses up the `query_id`. Outcome field is `reason` only for `rejected` |
