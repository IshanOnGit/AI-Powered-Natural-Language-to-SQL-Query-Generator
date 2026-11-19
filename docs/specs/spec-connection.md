# Spec: Connection

**Status:** Draft 1
**Depends on:** `intent.md`
**Used by:** Schema Extraction, Query Execution

---

## 1. Purpose

The Connection component takes the details the user types in (host, database, username, password), opens a connection to their database, and hands that open connection to the rest of the app.

It owns:
- Building the connection safely for PostgreSQL, MySQL and SQLite
- Making the connection **read-only**
- Keeping credentials safe
- Turning connection failures into clear messages

### What it does NOT do
- It does not read the schema. That is **Schema Extraction's** job.
- It does not check or run queries. That is **Query Validation** and **Query Execution**.

```
User details --> Connection (open, read-only) --> Schema Extraction
                                              --> Query Execution
```

---

## 2. Input

```python
@dataclass
class ConnectionInput:
    db_type: str                 # "postgresql" | "mysql" | "sqlite"
    host: str | None = None      # not used for SQLite
    port: int | None = None      # default: 5432 (Postgres), 3306 (MySQL)
    database: str | None = None  # database name, or file path for SQLite
    username: str | None = None
    password: SecretStr | None = None
```

- For **SQLite**, only `database` (the file path) is needed.
- `password` is wrapped in a small `SecretStr` type whose printed form is always `"***"`. A password can't leak by accident through `print`, logging, or an error message.
- Input is checked before connecting. Missing required fields raise `InvalidInput` with a clear message ("Host is required for PostgreSQL").

---

## 3. Output

```python
class Connection:
    db_type: str        # "postgresql" | "mysql" | "sqlite"
    engine: Engine      # SQLAlchemy engine, already tested and read-only

    def close(self) -> None: ...
```

```python
def connect(input: ConnectionInput) -> Connection:
    """Open a read-only connection, or raise a DBConnectionError."""
```

(The base error class is named `DBConnectionError`, to avoid clashing with Python's built-in `ConnectionError`.)

---

## 4. How the Connection Is Built

| Database | Driver | Notes |
|---|---|---|
| PostgreSQL | `psycopg2` | Default port 5432 |
| MySQL | `pymysql` | Default port 3306 |
| SQLite | built-in `sqlite3` | File path instead of host/port |

- The connection URL is built with SQLAlchemy's `URL.create(...)`, **never by joining strings**. This way special characters in a password (`@`, `/`, `#`) can't break the URL or be misread.
- **Connect timeout: 10 seconds.** After that, stop and raise `Unreachable`.
- After connecting, run a tiny test query (`SELECT 1`) to confirm it really works.
- The pool is kept minimal (1 connection, no overflow). Connection pooling for many users is Phase 2–3 in `intent.md`.

---

## 5. Read-Only Protection (the second lock)

Query Validation checks the SQL text (SELECT-only). This section adds a second, independent lock: the **database itself** is told to refuse changes for this connection.

| Database | How |
|---|---|
| PostgreSQL | Set `default_transaction_read_only = on` when the connection opens |
| MySQL | Run `SET SESSION TRANSACTION READ ONLY` when the connection opens |
| SQLite | Open the file in read-only mode (`mode=ro` in the connection URI) |

Why both locks: validators can have gaps (odd syntax, a function with side effects). If one lock fails, the other still stops the damage.

**What this cannot do:** it can't stop a determined user from turning read-only off *inside their own session* on some databases, and it can't replace giving the app a **read-only database user**. The UI should recommend using a read-only user. Automatically checking whether the user has write rights is deferred (section 9).

---

## 6. Credentials

Follows `intent.md` (Credential Handling):

- **Never logged** — not in console, not in log files.
- **Never in error messages.** Driver errors often include the connection string; our messages are written from our own fixed text, never copied from the driver.
- **Memory only.** Credentials live in memory for the active session and are not saved to disk or to the browser's storage.
- **Discarded on disconnect** and when the app closes.
- Never part of a URL query string, never committed. The app never saves *user-entered* database credentials in `.env` or anywhere else. (`.env` holds the LLM settings such as the Gemini key. Test-database details for the benchmark may also live there, never real ones.)
- V1 runs locally, so the password travels from the browser to the local backend only. Public deployment will need HTTPS and its own spec.

---

## 7. Errors

Same pattern as the other specs: one base class, simple errors, friendly messages.

| Error | Meaning | Example message |
|---|---|---|
| `InvalidInput` | Required field missing or invalid | "Host is required for PostgreSQL." |
| `AuthFailed` | Wrong username or password | "Login failed. Check your username and password." |
| `Unreachable` | Can't reach the server, or no answer within 10 seconds | "Can't reach the database at that host and port." |
| `DatabaseNotFound` | Server is reachable but database/file doesn't exist | "Database 'x' was not found." |
| `UnsupportedDatabase` | `db_type` not one of the three | "Only PostgreSQL, MySQL and SQLite are supported." |
| `Unknown` | Anything else | "Something went wrong connecting." |

All share the base class `DBConnectionError`, a subclass of the project-wide `AppError(code, message)` (see `spec-api.md`, section 7). Codes are prefixed, e.g. `db_auth_failed`. **No automatic retry** — the user fixes the details and tries again.

---

## 8. Lifecycle

- **One connection at a time** (intent.md scope). Connecting to a new database closes the old one first.
- A **Disconnect** action closes the connection and discards the credentials.
- If the connection drops later, Schema Extraction and Query Execution report their own errors (`ConnectionLost`); Connection does not silently reconnect in V1.

---

## 9. Testing

- **SQLite fixture (Chinook):** connects with no server needed — the fast default for unit tests.
- **PostgreSQL / MySQL:** integration tests, run only when those servers are available.
- **Wrong password, wrong host, wrong database name:** each gives the matching error from section 7.
- **Password never leaks:** force every error type and check the password text is not in the message, the error's printed form, or any log line.
- **Special characters in password** (`p@ss/w#rd`) connect correctly.
- **Read-only works:** after connecting, try an `INSERT` directly through the engine — it must fail on all three databases. This tests the second lock on its own, without involving the validator.
- **Missing fields:** each missing required field gives `InvalidInput`.

---

## 10. Deferred (not in V1)

- Checking whether the connected user has write rights, and warning about it
- SSL/TLS options and SSH tunnels
- Saving connection profiles (would need encrypted storage)
- Connection pooling and multi-user support (Phase 2–3)
- Restricting which hosts can be connected to (needed before public deployment — a public "connect to any host" form could be used to probe other servers)
- More database engines

---

## 11. Open Questions

1. **Backend framework.** Not decided yet (FastAPI is a likely fit). It affects how `Connection` is stored per session, but not this spec's contents.
2. **Read-only on MySQL.** `SET SESSION TRANSACTION READ ONLY` should be verified against the MySQL version used for testing.

---

## 12. Interview Note

**What this taught me:** safety works best in layers that don't depend on each other. A SQL validator can have gaps, so I also made the database connection itself read-only, and I test that layer on its own.

**What I'd say in an interview:** "I didn't rely on one check to stop destructive queries. The validator rejects non-SELECT statements, and separately the connection is opened read-only at the database level, with its own test. I also wrapped the password in a type that can't be printed, because driver error messages often include the connection string."

---

## 13. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: input/output, URL building, read-only protection, credentials, errors, lifecycle, testing |
| Draft 1.1 | Clarified `.env` wording; merged connect `Timeout` into `Unreachable`; errors build on shared `AppError` |
| Draft 1.2 | Built. SQLite: a missing file gives `DatabaseNotFound`, and a file that is not a database gives `InvalidInput` (`SELECT 1` alone would not catch it). `close()` also drops the engine (and so the password) and can be called twice. "One connection at a time" is kept by the API session, not by this component. PostgreSQL/MySQL read-only settings are set through driver options (`default_transaction_read_only`, `init_command`) and are tested against a real server only when `TEST_*` variables are set |
