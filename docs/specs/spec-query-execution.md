# Spec: Query Execution

**Status:** Draft 1
**Depends on:** `spec-query-validation.md` (supplies `ValidatedQuery`), `spec-connection.md` (supplies the connection)
**Used by:** API, benchmark

---

## 1. Purpose

Query Execution runs an approved, validated query and returns the result in a form the UI can show as a table.

It owns:
- Running the query with a time limit
- Turning database values into plain, display-safe values
- Turning database failures into clear messages

### What it does NOT do
- It does not check whether SQL is safe. That is **Query Validation**.
- It does not decide *whether* to run. The user's approval happens in the API/UI flow (`spec-api.md`).
- It does not add or cap `LIMIT`. Validation already guarantees the limit, so there is **one place** for that rule.

---

## 2. Input and Output

```python
def execute(connection: Connection, query: ValidatedQuery) -> ExecutionResult: ...

@dataclass
class ExecutionResult:
    columns: list[str]
    rows: list[list]          # each value: str, int, float, bool or None
    row_count: int            # len(rows)
    elapsed_ms: int
```

- It accepts **only a `ValidatedQuery`**, never a plain string (see validation spec, section 2).
- A query that returns **zero rows** is a normal success: `columns` filled, `rows` empty. The UI says "No rows returned."

---

## 3. Time Limit

One setting: `QUERY_TIMEOUT_SECONDS`, default `15`.

The database is asked to stop the query itself, so it doesn't keep working after we give up:

| Database | How |
|---|---|
| PostgreSQL | `SET statement_timeout` for the session |
| MySQL | `SET SESSION max_execution_time` (milliseconds; applies to SELECT) |
| SQLite | No built-in timeout. A timer calls the connection's `interrupt()` after the limit. |

When the limit is hit, raise `Timeout`. **No retry.**

---

## 4. Making Values Display-Safe

Database values are converted so the result can be sent as JSON and shown in a table:

| Database value | Becomes |
|---|---|
| `NULL` | `None` (UI shows an empty cell or "NULL") |
| integers, floats, booleans, text | unchanged |
| `Decimal` | string (keeps exact digits, e.g. `"12.50"`) |
| dates, times, timestamps | ISO text, e.g. `"2025-03-01"` |
| binary data (`bytes`) | the text `"<binary, 2048 bytes>"` |
| anything else (arrays, JSON, UUID…) | its text form |
| text longer than 1,000 characters | cut at 1,000 characters, ending with `…` |

The cut on long text keeps one huge value from making the response heavy. It applies to display only; nothing is changed in the database.

---

## 5. Safe Running

- Nothing is ever committed. The query only reads, and the connection is closed or reset without saving anything.
- The read-only connection from `spec-connection.md` is the database-level lock. This spec does not add a separate one.
- V1 is single-user and the UI blocks a second request while one is running, so there is no separate "busy" handling here.

---

## 6. Errors

Same pattern as the other specs: one base class `ExecutionError`, friendly messages, no raw driver text with sensitive parts.

| Error | Meaning | Example message |
|---|---|---|
| `Timeout` | Over the time limit | "The query took too long and was stopped." |
| `QueryFailed` | The database rejected it (unknown column, type problem, etc.) | "The database couldn't run this query: column "x" does not exist." |
| `ConnectionLost` | Connection dropped | "Lost the connection to the database." |
| `Unknown` | Anything else | "Something went wrong running the query." |

- For `QueryFailed`, the **database's short error text is shown** (trimmed to 300 characters) because it is the most useful feedback ("column does not exist"). The message never includes the connection details. A write that the read-only connection refuses also ends up here: it should never happen if validation works, and if it does, the test list below is where the gap gets fixed.
- No automatic retry in V1; the user can rephrase and try again.
- `ExecutionError` is a subclass of the project-wide `AppError(code, message)` (see `spec-api.md`, section 7). Codes are prefixed, e.g. `query_timeout`.

---

## 7. Testing

- **Chinook in SQLite:** normal query, empty result, `NULL`s, a date column, a Decimal/real column.
- **Timeout:** a deliberately slow query (e.g. a large cross join) stops within a few seconds of the limit, on SQLite; PostgreSQL and MySQL as integration tests.
- **Failure:** a query with a wrong column name gives `QueryFailed` with the database's message.
- **Write refused:** a `ValidatedQuery` built by hand for an `INSERT` (bypassing validation, in tests only) fails with `QueryFailed` on all three databases, and nothing changes.
- **Value conversion:** one test per row of the table in section 4, including a 2,000-character string and a binary column.
- **No credentials** in any error message.

---

## 8. Deferred (not in V1)

- Streaming or paging through large results
- Cancelling a running query from the UI
- Exporting results (CSV)
- Query history and audit logging (Phase 2)
- Cost estimation with `EXPLAIN` (Phase 4)

---

## 9. Interview Note

**What this taught me:** stopping a slow query is different from giving up waiting for it. Asking the database to enforce the timeout itself means a runaway query doesn't keep using the server.

**What I'd say in an interview:** "I set the timeout inside the database for PostgreSQL and MySQL, and interrupt the connection for SQLite, so a slow generated query is actually stopped. And the connection is read-only, so even a query that slipped past validation couldn't change data."

---

## 10. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: result shape, timeout per database, value conversion, rollback, errors, testing |
| Draft 1.1 | Simplified: removed `WriteBlocked`, `Busy` and the rollback requirement; errors build on shared `AppError` |
| Draft 1.2 | Built. `QUERY_TIMEOUT_SECONDS` may be a decimal (for example `0.5`). The SQL is sent exactly as validated (no bind-parameter or `%` handling), so `LIKE '%a%'` and text like `'12:30'` are safe. `NaN` and infinity are shown as text because JSON cannot carry them. Long text is cut to its first 1,000 characters plus `…`; the same cut applies to Decimal and other text forms. A closed connection gives `ConnectionLost`. The database's error text has the connection password scrubbed out before it is shown. PostgreSQL and MySQL timeouts, and value types, are tested only against a real server when the `TEST_*` variables are set |
