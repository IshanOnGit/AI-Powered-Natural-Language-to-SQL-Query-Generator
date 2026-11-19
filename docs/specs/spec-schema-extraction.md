# Spec: Schema Extraction

**Status:** Draft 1
**Depends on:** `intent.md`, `spec-connection.md` (supplies the open, read-only database connection)
**Used by:** Generate Query (`schema_text`), benchmark

---

## 1. Purpose

Schema Extraction reads the structure of the connected database and turns it into a **compact text block** (`schema_text`) that Generate Query pastes into the prompt. The schema is used internally only — V1 does not display it to the user.

Internally it first builds a small structured object (`SchemaInfo`), then renders that to text. The structure exists to keep rendering and testing simple, not for the UI.

It reads **structure only** (tables, columns, types, keys, relationships). It never reads row data.

### What it does NOT do
- It does not open or manage the connection. That is the **Connection** component's job. It receives a connection that is already open.
- It does not build prompts or decide what to send to the model. That is **Generate Query's** job.
- It does not cache anything in V1 (see section 9).

```
Connection (open)
      |
      v
Schema Extraction --> SchemaInfo (internal)
      |
      v
  render_text() --> schema_text --> Generate Query
```

---

## 2. Input and Output

```python
def extract_schema(connection: Connection) -> SchemaInfo:
    """Read the schema, or raise a SchemaError."""
```

`db_type` comes from the connection (`"postgresql" | "mysql" | "sqlite"`), so the caller does not pass it twice.

```python
@dataclass
class Column:
    name: str
    type: str               # simplified, e.g. "VARCHAR", "INTEGER" (see section 5)
    nullable: bool
    primary_key: bool

@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]

@dataclass
class Table:
    name: str
    kind: str               # "table" | "view"
    columns: list[Column]
    foreign_keys: list[ForeignKey]

@dataclass
class SchemaInfo:
    db_type: str
    tables: list[Table]
    warnings: list[str]     # e.g. "Schema has 34 tables; V1 is tested up to ~20."
```

`SchemaInfo` has one method, `render_text() -> str`, which produces `schema_text` (section 5).

---

## 3. How It Reads the Schema

One code path for all three databases, using **SQLAlchemy's `Inspector`** (`get_table_names`, `get_view_names`, `get_columns`, `get_pk_constraint`, `get_foreign_keys`). SQLAlchemy already hides the differences between `information_schema` (Postgres/MySQL) and `PRAGMA` (SQLite).

Why not hand-written queries per database: three dialects means three code paths to test and keep in sync. The Inspector gives one path, and the dialect differences become the library's problem.

### Scope rules
| Database | What is read |
|---|---|
| PostgreSQL | The `public` schema only (other schemas are deferred) |
| MySQL | The database named in the connection |
| SQLite | The `main` database |

- **Views are included**, marked `kind="view"`. Users often ask questions that a view answers.
- **System objects are excluded**: `pg_*`, `information_schema`, `sqlite_*`, MySQL `mysql`/`sys`/`performance_schema`.
- Tables and columns are sorted **alphabetically**, so the output is the same every run. This matters for the benchmark and for golden-file tests.

---

## 4. Size Limits

Intent assumes up to roughly 20 tables in V1.

- **Up to 20 tables:** normal.
- **Over 20 tables:** still extracted in full, but a warning is added to `SchemaInfo.warnings` and passed back to the caller (the app may show it as a notice; the schema itself is not shown). No silent truncation — dropping tables would make some questions unanswerable with no explanation.
- **Zero tables:** raises `EmptySchema` (section 7).

If the text turns out too long for the chosen model, that is reported by the provider as `ContextTooLong` (provider spec), not handled here. Smart filtering of large schemas is Phase 4.

---

## 5. Text Format (`schema_text`)

Compact, one table per block, relationships stated explicitly. Names/types/keys only — **no sample rows, no comments** (token discipline, matching Generate Query section 5).

```
TABLE customers
  customer_id INTEGER PRIMARY KEY
  email VARCHAR NOT NULL
  support_rep_id INTEGER
  FOREIGN KEY (support_rep_id) -> employees(employee_id)

VIEW top_customers
  customer_id INTEGER
  total_spent NUMERIC
```

Rules:
- Types are **simplified to the base name**: `VARCHAR(120)` becomes `VARCHAR`, `NUMERIC(10,2)` becomes `NUMERIC`. Lengths add tokens without helping the model write queries. SQLite's free-form types are passed through as declared.
- `NOT NULL` is shown only when true; nullable is the default and left unstated.
- `PRIMARY KEY` is shown on the column. For composite keys, each member column gets it.
- Foreign keys are listed at the end of the table block, so the model can see join paths.
- Format is **fixed** — the same schema always renders the same text.

---

## 6. Prompt Injection Safety

Intent.md flags that odd table/column names could try to manipulate the model. Generate Query already tells the model the schema is data. This spec adds the extraction-side defence:

- Every name is rendered through a fixed template, one per line.
- Names containing **newlines or control characters** have those characters replaced with `_`, and a warning is added ("1 identifier was sanitised"). A name cannot add a new line to the prompt.
- Names that are **not plain identifiers** (spaces, quotes, punctuation) are wrapped in double quotes in the output, so the model also writes them correctly in SQL.
- Comments are **not extracted** in V1. They are free-form text written by whoever owns the database, which makes them the easiest injection route. They would help accuracy, so this is a known trade-off (section 10).

This reduces the risk; it does not remove it. The query validator (SELECT-only) remains the real safety net.

---

## 7. Errors

Same pattern as the provider spec: one base class, simple errors, friendly messages. Raw driver errors never reach the user.

| Error | Meaning | Example message |
|---|---|---|
| `EmptySchema` | No tables or views found | "No tables found. Check the database or schema name." |
| `PermissionDenied` | User can't read metadata | "This user can't read the database structure." |
| `ConnectionLost` | Connection dropped mid-read | "Lost connection while reading the schema." |
| `Timeout` | Read took longer than the limit | "Reading the schema took too long." |
| `Unknown` | Anything else | "Something went wrong reading the schema." |

All share the base class `SchemaError`, a subclass of the project-wide `AppError(code, message)` (see `spec-api.md`, section 7). Codes are prefixed, e.g. `schema_timeout`.

- **Timeout:** 15 seconds for the whole extraction. The clock is checked before each table is read, so one database call that hangs forever is not interrupted here (the connection's own 10 second timeout covers a dead server).
- **No retries.** A failed read is shown to the user, who can reconnect.
- **Credentials never appear in any error message, warning, or log line** (intent.md, Credential Handling). Driver errors often echo the connection string, so messages are built from our own fixed text, never from the driver's.

---

## 8. Testing

- **Golden-file tests:** extract from a small fixture database and compare `render_text()` to a saved expected file. Same input must give identical output.
- **Fixtures:** Chinook in SQLite, PostgreSQL and MySQL (cross-dialect check — all three must give the same logical tables, columns and foreign keys). Sakila/Pagila for the second, unrelated domain.
- **Hostile-name test:** a database with a newline in a table name and an instruction-like column name ("ignore previous instructions") — output must stay in the fixed template and carry a warning.
- **Edge cases:** empty database, view-only database, composite primary key, composite foreign key, table with no primary key, missing permissions.
- **Credential test:** force a connection error and check the connection password does not appear in the error message.
- SQLite in-memory databases are used for the fast unit tests; PostgreSQL/MySQL runs are integration tests, only when those servers are available.

---

## 9. Schema Drift and Caching

V1 extracts **once per connection** and keeps the result in memory for the session. A **"Refresh schema"** action (or simply reconnecting) extracts again. Nothing is written to disk.

This does not fully solve schema drift (intent.md risk): if the database changes mid-session, queries may refer to old structure until the user refreshes. Automatic cache invalidation is Phase 2.

---

## 10. Deferred (not in V1)

- Table/column **comments** (accuracy gain, but an injection route — revisit with sanitising rules)
- Sample values and row counts
- Indexes, unique constraints, check constraints, enum values
- Multiple Postgres schemas at once
- Schema caching and drift detection (Phase 2)
- Filtering or ranking tables for large schemas (Phase 4)

---

## 11. Open Questions

1. **Views in the prompt.** Included by default. If the benchmark shows views confuse the model, make this a setting.
2. **Type simplification.** Dropping lengths is assumed harmless; the benchmark should confirm.

---

## 12. Interview Note

**What this taught me:** the schema is the one part of the prompt that comes from outside my control, so it needs the same suspicion as user input. Fixing the output format and sanitising names matters as much as reading the schema correctly.

**What I'd say in an interview:** "I used SQLAlchemy's Inspector so one code path covers Postgres, MySQL and SQLite, and I render the schema into a fixed, sorted text format so results are repeatable for the benchmark. Because table and column names end up inside an LLM prompt, I treat them as untrusted input — sanitised, templated, and with comments deliberately left out in V1."

---

## 13. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: text output, Inspector-based extraction, size limits, injection safety, errors, testing, drift handling |
| Draft 1.1 | Schema is used internally only; removed UI display of schema (and matching wording in intent.md) |
| Draft 1.2 | Simplified: PostgreSQL reads `public` only (no options), dropped name truncation and `SchemaNotFound`; errors build on shared `AppError` |
| Draft 1.3 | Built. Timeout is checked between tables (not a hard stop). Takes any object with `db_type` and `engine`, so it was built before Connection. Declared types are read through SQLAlchemy, so an unknown SQLite type shows no type. Hostile declared types are sanitised and capped at 40 characters |
