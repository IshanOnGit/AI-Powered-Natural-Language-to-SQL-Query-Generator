# Spec: Query Validation

**Status:** Draft 1
**Depends on:** `spec-generate-query.md` (supplies the SQL), `spec-connection.md` (the second, database-level lock)
**Used by:** Query Execution

---

## 1. Purpose

Query Validation is the first safety lock. It takes the SQL the model wrote and decides: **safe to show the user and run, or rejected**. It also makes small safe changes (adds a row limit).

It owns:
- Checking the SQL is valid and is a single read-only `SELECT`
- Blocking dangerous statements, clauses and functions
- Adding or capping `LIMIT`
- Producing the final SQL the user will approve

### What it does NOT do
- It does not run the query or set timeouts. That is **Query Execution's** job.
- It does not check that tables and columns exist (section 9). A missing table simply fails at run time with a clear database error.
- It is **not the only protection.** The connection is also read-only at the database level (`spec-connection.md`, section 5). This spec assumes it can have gaps.

```
Generate Query --sql--> Query Validation --ValidatedQuery--> user approval --> Query Execution
                              |
                              +--rejected + reason--> user
```

---

## 2. Input and Output

```python
def validate(sql: str, db_type: str) -> ValidationResult: ...

@dataclass
class ValidatedQuery:
    sql: str                 # final SQL: exactly what the user sees and what will run
    changes: list[str]       # e.g. ["Added LIMIT 100"]

@dataclass
class ValidationResult:
    ok: bool
    query: ValidatedQuery | None = None
    reason: str | None = None     # a code from section 5
    message: str | None = None    # friendly text for the user
```

A rejection is a **normal result, not an exception** — a model writing a bad query is expected, not exceptional.

**Query Execution accepts only a `ValidatedQuery`**, never a plain string. The only way to get one is through `validate()`, so unvalidated SQL can't reach the database by mistake.

---

## 3. How It Checks: Parse, Don't Search Text

The SQL is parsed into a syntax tree with **sqlglot**, using the dialect of the connected database (PostgreSQL, MySQL or SQLite). Rules are applied to the **tree**, not to the raw text.

Why not search for words like `DELETE` or `;` in the text:
- `SELECT 'a; DROP TABLE x'` is a harmless query (the `;` is inside a string). A text search wrongly rejects it.
- `SELECT update_date FROM orders` is harmless (`update` is part of a column name). A text search wrongly rejects it.
- `SELECT 1 /* ; DROP TABLE x */` is harmless (comment). Text search gets confused.
- Danger can hide in places a text search doesn't expect, such as a `DELETE` inside a `WITH` clause (section 4, rule 4).

A parser sees the structure, so it avoids both false alarms and misses.

---

## 4. Rules (applied in order)

1. **Not empty.** Empty input (or only a comment) is rejected.
2. **Must parse.** If sqlglot can't parse it in the connected database's dialect, reject as a syntax error.
3. **Exactly one statement.** Two or more statements are rejected. A single trailing `;` is allowed and removed.
4. **Must be a read-only query.** The top level must be a `SELECT`, or a set of selects joined with `UNION` / `INTERSECT` / `EXCEPT`. `WITH ... SELECT` is allowed. Then the whole tree is checked, and the query is rejected if **anywhere inside it** there is an insert, update, delete, merge, create, alter, drop, or similar. This catches a data-changing statement hidden inside a `WITH`.
5. **No forbidden clauses:**
   - `SELECT ... INTO` (creates a table in PostgreSQL; writes a file in MySQL via `INTO OUTFILE` / `INTO DUMPFILE`)
   - Row locking: `FOR UPDATE`, `FOR SHARE`, `LOCK IN SHARE MODE`
6. **No forbidden functions** (case-insensitive). This list is a *denylist*, which can never be complete, so it only covers well-known harmful ones:

   | Database | Examples |
   |---|---|
   | PostgreSQL | `pg_sleep`, `pg_read_file`, `set_config`, `dblink` |
   | MySQL | `sleep`, `benchmark`, `load_file` |
   | SQLite | `load_extension`, `readfile`, `writefile` |

   The list is kept short on purpose: the read-only connection and the time limit already cover most of these risks.

7. **Apply the row limit** (section 6).

The first rule that fails stops the check. The user sees that one reason.

---

## 5. Rejection Reasons

| Code | Meaning | Example message |
|---|---|---|
| `EMPTY` | Nothing to check | "No query to check." |
| `SYNTAX_ERROR` | Can't be parsed | "The query isn't valid SQL for this database." |
| `MULTIPLE_STATEMENTS` | More than one statement | "Only one query at a time is allowed." |
| `NOT_READ_ONLY` | Not a SELECT, or contains a write | "Only read-only SELECT queries are allowed." |
| `FORBIDDEN_CLAUSE` | `INTO`, `FOR UPDATE`, etc. | "This query uses a clause that isn't allowed." |
| `FORBIDDEN_FUNCTION` | Function on the denylist | "This query uses a function that isn't allowed." |

Messages are written from fixed text. They don't echo the SQL back as an instruction. They can name the offending keyword or function in a quoted, non-executable way.

A rejection is shown to the user like any other outcome, never silently dropped (consistent with the Ambiguous Query Policy in `intent.md`). There is **no automatic retry** in V1 (matches Generate Query, section 6).

---

## 6. Row Limit

Settings (from environment variables, same pattern as the provider spec):

| Setting | Default | Meaning |
|---|---|---|
| `QUERY_DEFAULT_LIMIT` | `100` | `LIMIT` added when the query has none |
| `QUERY_MAX_LIMIT` | `1000` | Highest `LIMIT` allowed |

- **No `LIMIT` on the outermost query:** add `LIMIT 100`. Record `"Added LIMIT 100"` in `changes`.
- **`LIMIT` above the maximum:** lower it to 1000. Record `"Lowered LIMIT from 5000 to 1000"`.
- **`LIMIT` at or below the maximum:** leave it alone.
- Only the **outermost** `LIMIT` matters. A `LIMIT` inside a subquery doesn't limit the final result.
- An existing `OFFSET` is kept.
- The limit is added by editing the syntax tree, so it is always valid for the dialect.

---

## 7. The Final SQL

The SQL in `ValidatedQuery.sql` is **regenerated from the syntax tree** in the connected dialect, with comments removed and the trailing `;` dropped. It can therefore look slightly different from the model's text (spacing, capitalisation, quoting).

- **What the user approves is exactly what runs.** There is no step between approval and execution that changes the SQL.
- **V1 has no editing of the SQL by the user.** If editing is added later, the edited SQL must go through `validate()` again.
- The accuracy benchmark compares **query results**, not SQL text, so rewording doesn't affect scores.

---

## 8. Testing

All with plain strings. No database or model needed.

**Must be accepted (and not wrongly rejected):**
- Simple `SELECT`, joins, aggregates, subqueries
- `WITH ... SELECT` (CTE)
- `UNION` of two selects
- `SELECT 'a; DROP TABLE x'` (semicolon in a string)
- `SELECT update_date, delete_flag FROM t` (keywords inside names)
- `SELECT 1 /* ; DROP TABLE x */` (semicolon in a comment)
- Trailing `;`

**Must be rejected:**
- `DROP TABLE x`, `DELETE FROM x`, `UPDATE x SET a=1`, `INSERT ...`, `TRUNCATE`, `ALTER`, `CREATE`
- `SELECT 1; DROP TABLE x` (two statements)
- `WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d` (write hidden in a CTE)
- `SELECT * INTO new_table FROM t`, `SELECT ... INTO OUTFILE '/tmp/x'`
- `SELECT * FROM t FOR UPDATE`
- `SELECT pg_sleep(100)`, `SELECT load_file('/etc/passwd')`, `SELECT load_extension('x')`
- `COPY ...`, `ATTACH DATABASE ...`, `PRAGMA ...`, `SET ...`, `SHOW ...`
- `EXPLAIN ANALYZE DELETE FROM t`
- Empty string, only a comment, text that isn't SQL

**Limit behaviour:** no limit gets one; 5000 is lowered; 50 is untouched; limit only inside a subquery still gets an outer limit; `UNION` gets a limit on the whole result; `OFFSET` survives.

**Dialect:** the same checks run for PostgreSQL, MySQL and SQLite versions of the queries (MySQL backticks, PostgreSQL double quotes).

**Combined check (integration):** every "must be rejected" query is also sent straight to the read-only connection to confirm the **second lock** blocks it on its own. This proves the validator isn't the only protection.

---

## 9. Deferred (not in V1)

- **Checking tables and columns exist** against the schema before running (better error messages; a missing table currently fails at run time)
- Allowing `EXPLAIN`, `SHOW`, `DESCRIBE` (read-only but not `SELECT`; Phase 4 for query cost estimation)
- Making the function denylist configurable
- Limits on query complexity (many joins, huge `IN` lists)
- Automatic retry with error feedback to the model (Phase 2, as in Generate Query)
- User editing of the SQL before approval

---

## 10. Open Questions

1. **sqlglot vs. the real database parser.** If sqlglot and the database read the same text differently, something could pass the validator that the database treats differently. This is a known class of weakness for any validator. The read-only connection (second lock) and the execution timeout are the answer to it, which is why they are required rather than optional.
2. **Row limit values.** `100` and `1000` are starting guesses; adjust after seeing the Chinook and Sakila benchmark questions.

---

## 11. Note on Generate Query

Generate Query (section 4) accepts a reply as SQL if it starts with `SELECT` **or** `WITH`, and leaves the real decision to Query Validation. This matches rule 4 above, which allows `WITH ... SELECT`.

---

## 12. Interview Note

**What this taught me:** checking text for scary keywords is both too strict and too weak — it rejects harmless queries and still misses things like a `DELETE` hidden inside a `WITH`. Parsing the SQL into a tree and checking the structure is the right tool. I also learned not to trust any single check.

**What I'd say in an interview:** "I validate SQL by parsing it with sqlglot rather than using regex, so a semicolon inside a string isn't mistaken for a second statement and a write hidden inside a CTE doesn't slip through. Validation is the first of two locks — the connection itself is read-only too — and I test that second lock separately, because I assume any validator can have gaps."

---

## 13. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: parse-based validation, rules, rejection reasons, row limit, final SQL guarantee, testing, deferred items |
| Draft 1.1 | Shortened the function denylist; removed the length check |
| Draft 1.2 | Built. `ValidatedQuery` carries a private seal, so only `validate()` can create one. MySQL `INTO OUTFILE` does not parse in sqlglot, so it is found with the tokenizer and still gets `FORBIDDEN_CLAUSE`. The final SQL is parsed again as a last check. A `LIMIT` that is not a plain number (negative, `FETCH ... PERCENT`) is replaced with the default limit; `LIMIT ALL` is read by sqlglot as no limit, so it gets the default limit. Denylists include a few obvious variants (`pg_sleep_for`, `dblink_exec`, `lo_import`, `lo_export`, ...). Comments (including MySQL `/*! ... */`) are dropped when the SQL is rebuilt, so the database only runs what was checked. Bad limit settings raise `AppError` with code `query_setup_problem` |
