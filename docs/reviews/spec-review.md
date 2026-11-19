# Spec Review — Text-to-SQL Project

Review of all 9 spec files: do they agree with each other, is anything over-built for a ~1 month solo project, and where will the work get stuck.

## Status: changes applied (2026-10-09)

You approved the suggestions, so all of C1–C10 and the simplifications O1, O2, O3, O7, O8 and O11 are now applied. O4, O5, O6 and O9 were "keep, but small" and were trimmed (name truncation and the long function list removed). I also removed the validator's 10,000-character check, since it guarded a case that is unlikely and adds one more rule to test.

Decisions I made for you, where you hadn't answered (change any of these):
- **Frontend:** plain HTML/JS served by the FastAPI backend. No framework, no build step.
- **Gemini:** `gemini-2.5-flash`. **Ollama:** a Qwen 2.5 model; pin the exact size before the first benchmark.
- **Speed goal:** "typically within ~10 seconds with Gemini"; Ollama speed is measured and reported, not promised.
- **Row limits:** kept at 100 default / 1000 max.

The sections below are the original review, kept as it was written. Anything marked C or O has been dealt with as above.

---

## 1. The whole system in one picture

```
 Browser (UI)
     |  question                          ^ result table
     v                                    |
 API  --connect-->  Connection (read-only lock #2)
  |                      |
  |                      v
  |               Schema Extraction --> schema_text
  |
  +--generate-->  Generate Query --(Provider: Gemini | Ollama)--> reply text
  |                      |
  |                      v
  |               Query Validation (lock #1) --> ValidatedQuery + query_id
  |
  +--execute(query_id)--> Query Execution --> rows

 Benchmark = runs the same pipeline from the command line, compares with reference answers
```

**Safety in short:** the model's SQL is parsed and checked (lock 1), the database connection itself is read-only (lock 2), the browser can only approve an ID, never send SQL, and every query has a time limit.

---

## 2. What each spec contains (simplest version)

| # | Spec | What it is, in one line | Main decisions | Build effort* |
|---|---|---|---|---|
| 0 | `intent.md` | The goal, scope, risks, success criteria | Local-first; Gemini + Ollama; Chinook + Sakila; no schema display | — |
| 1 | `spec-llm-provider-interface` | A thin wrapper so the app can use Gemini or Ollama the same way | One `generate()` method; 8 friendly errors; 1 retry max; settings from `.env`; fake provider for tests | M |
| 2 | `spec-generate-query` | Builds the prompt, reads the model's reply | Plain-text reply with `CLARIFY:` / `NOT_FOUND:` markers, not JSON; strips `<think>`; no retry | M–L (prompt tuning is the real work) |
| 3 | `spec-connection` | Opens the database connection, makes it read-only, protects the password | SQLAlchemy URLs; read-only per database; `SecretStr` password; one connection at a time | S–M |
| 4 | `spec-schema-extraction` | Reads tables/columns/keys and turns them into prompt text | SQLAlchemy Inspector; fixed sorted text format; names sanitised; no comments or sample rows | M |
| 5 | `spec-query-validation` | Lock #1: allows only a single safe SELECT, adds a LIMIT | Parse with sqlglot (not text search); function denylist; limit 100 default / 1000 max | M |
| 6 | `spec-query-execution` | Runs the checked query with a time limit, makes results display-safe | Timeout inside the database; rollback always; value conversion table | S–M |
| 7 | `spec-api` | The endpoints, plus the approval mechanism | `/execute` accepts only a `query_id`; single-use, expires in 10 min; framework error handler must not echo passwords | M |
| 8 | `spec-ui` | One page: connect, ask, review SQL, approve, results | No schema shown; SQL read-only; clarification = rewrite question | M |
| 9 | `spec-benchmark` | Command-line accuracy test, separate for Gemini and Ollama | Result comparison, not SQL text; 8–10 questions written before first run; `wrong_result` reported on its own | M–L (fixtures + questions) |

*Effort is my rough guess to help you plan, not a measured number.

---

## 3. Contradictions and gaps (should be fixed)

Ordered by how much they would hurt if left alone.

| # | Problem | Where | Suggested fix |
|---|---|---|---|
| C1 | **Speed target vs timeouts.** `intent.md` wants an answer in about 5–10 seconds. The model call allows 30 seconds, with one retry (so up to about 60+). A local Ollama model on a laptop may well take longer than 10 seconds anyway. | intent, provider spec | Decide what the 5–10s promise means: "Gemini only", or "typical, not guaranteed". Show a "Generating…" state in the UI (already there). Possibly lower the Gemini timeout. |
| C2 | **Provider choice isn't consistent.** The provider spec has a single `LLM_PROVIDER` setting. The intent and API let the user pick per request. Also, the UI needs to know which providers are *available* (`/status`), but the provider spec has no way to ask "is Gemini configured / is Ollama running?". | provider, api, ui | Add to the provider spec: a `get_provider(name)` function and a cheap `is_available()` check. Keep `LLM_PROVIDER` only as the default. |
| C3 | **Generate Query is under-specified in two places.** (a) How the provider is passed in — its input has only question, schema and db type. (b) Which parts go into the provider's `system_prompt` vs `user_prompt`. | generate-query, provider | Say: system prompt = instructions + few-shot examples; user prompt = schema + question. Add `provider` as a parameter of `generate_query(...)`. |
| C4 | **The pipeline function depends on the API's session object**, so the benchmark would have to build a fake web session to use it. | api, benchmark | Make the pipeline take plain values: `(question, provider, connection or schema_text, db_type)`. The API passes in what it holds. |
| C5 | **Postgres schema option can't be reached.** The schema spec has `schema_name`, but neither the connection input nor the `/connect` body has a field for it. | schema, connection, api | Simplest: drop the option for V1 and always use `public`. |
| C6 | **`.env` statement clashes.** Connection spec: "`.env` is only for LLM settings". Benchmark spec: test database details come from `.env`. | connection, benchmark | Reword the connection spec: the app never stores *user-entered* credentials in `.env`; benchmark test credentials may be in `.env`. |
| C7 | **Same error names in four places.** `Timeout`, `Unknown`, `Unreachable` exist in provider, connection, schema and execution specs. Easy to confuse in code, and the API has to translate each separately. | provider, connection, schema, execution, api | One small shared base: `AppError(code, message)`, with modules adding their own codes (`gemini_timeout`, `db_timeout`…). The API then just returns `{code, message}`. |
| C8 | **"Rejected" wording.** A query that merely has a syntax error is shown as "This query was blocked", which sounds wrong. | ui, validation | UI message by reason code: syntax error → "The model wrote invalid SQL for this database"; safety reasons → "blocked". |
| C9 | **Housekeeping.** Intent says specs live in `/docs`, but all files are in the project root. Intent's first changelog line mentions an "architecture link" that doesn't exist. | intent | Move specs into `/docs` when the repo is created; delete or create the architecture reference. |
| C10 | **Privacy claim is incomplete.** Intent says Ollama is more private (schema and queries stay local). The converse isn't shown to the user: choosing Gemini sends the table/column names and the question to Google. | intent, ui | Add one line in the UI beside the provider choice. Tiny change; supports the project's own privacy story. |

---

## 4. Over-engineering check

Judged against: one developer, about one month, a portfolio piece. "Cut" means remove from V1; nothing here is dangerous to remove except where marked.

| # | Item | Verdict | Why |
|---|---|---|---|
| O1 | **`query_id` store** (dict, single-use, 10-minute expiry, cleared on refresh) | **Simplify** | The UI only ever has one pending query. Replace with a single "pending query" slot: a new Generate replaces it; Execute runs it once and clears it. Keeps the key safety property (browser never sends SQL) with much less code. |
| O2 | **`WriteBlocked` error** (detect and log when the database refuses a write) | **Cut or simplify** | Detecting "read-only violation" differs per driver (different codes and messages). Treat it as a normal `QueryFailed` and rely on the validator tests. |
| O3 | **Three layers of read-only** (validator, read-only connection, always-rollback) | **Keep two, drop the third** | The rollback is cheap, but adds a third idea to explain. The validator plus the read-only connection is the story. Keep rollback only if it costs nothing in the library you use. |
| O4 | **Schema: `SchemaInfo` dataclasses, then render to text** | **Keep, but small** | Now that the schema isn't displayed, the structured step is mostly for testing. It is still the easiest way to write golden-file tests. Don't add features to it. |
| O5 | **Identifier sanitising** (truncate to 64 chars, replace control characters, quote odd names) | **Keep, trim** | Replacing control characters and quoting odd names is cheap and a good interview point. The 64-character truncation can go. |
| O6 | **Function denylist** in the validator | **Keep, short** | Cheap, but it can never be complete, and the read-only connection plus timeout cover the same risks. Keep 3–5 well-known names per database. |
| O7 | **Provider: 8 error types** and a full conformance test suite | **Trim** | `Blocked` and `ContextTooLong` matter (Ollama context size is a real issue); `ModelNotFound` and `NotConfigured` could share one "setup problem" type. 5–6 types are enough. |
| O8 | **Benchmark cross-dialect run on PostgreSQL and MySQL** | **Shrink** | Needs two database servers loaded with Chinook (likely Docker). Suggest: headline accuracy on SQLite only; PG/MySQL get a small smoke test (2–3 questions) to prove the app connects and works on them. |
| O9 | **Benchmark `--runs`, JSON detail files, summary generator** | **Keep simple** | One run default is fine. Detail JSON is cheap and useful for debugging. Skip anything beyond a single Markdown summary table. |
| O10 | **Interview notes in every spec** | **Keep** | You asked for this lens in `intent.md`. They cost nothing. |
| O11 | **Execution-level concurrency check (`Busy`)** | **Cut** | The UI already blocks double requests, and V1 is single-user. |

If all of O1, O2, O3, O7, O11 are applied, the spec set shrinks a bit and so does the code, without weakening safety.

---

## 5. Bottlenecks (where time will actually go)

Not a spec problem, but a build-time problem.

1. **Prompt tuning and local-model quality.** The specs describe *what* the prompt contains, not the many rounds needed to make it work. Small Ollama models may only reach modest accuracy. Plan time for several prompt iterations, and be prepared for Ollama's score to be much lower than Gemini's (which is a fine, honest result).
2. **Ollama context window.** The 20-table schema plus instructions and examples may be several thousand tokens. Ollama's default context can be smaller than that and cuts the prompt silently. The provider spec already says to set `num_ctx` on purpose; check it early with the real Chinook/Sakila schema.
3. **Setting up the fixtures.** Chinook on three engines and a trustworthy Sakila SQLite port (the community port needs its data checked) is dull, unavoidable work that blocks the benchmark and the cross-database tests.
4. **Dialect differences in model output.** Models often write PostgreSQL-style functions for MySQL/SQLite, especially dates (`strftime` vs `date_trunc` vs `DATE_FORMAT`). Some will be rejected or fail at run time; the benchmark will show how much. The `db_type` in the prompt helps, but this is a real accuracy limit.
5. **Validator parsing gaps.** sqlglot may not parse every valid dialect-specific query, so some good queries will be rejected as syntax errors. The test list in the validation spec is the guard; expect to adjust it.
6. **Timeouts across three databases.** SQLite has no statement timeout, so it needs a timer calling `interrupt()` — the fiddliest piece in the execution spec.
7. **Question writing for the benchmark.** Ten good questions with unambiguous answers takes longer than it sounds, and the "don't tweak after first run" rule means getting them right first.

---

## 6. Decisions I need from you

These are the open questions scattered across specs, in one place.

1. **Frontend:** plain HTML/JS served by the backend, or React + Vite? (UI spec, section 8)
2. **Ollama models:** which one, which size, is it Qwen 2.5 or DeepSeek R1 7B for the headline result? (benchmark, section 11)
3. **Gemini model:** confirm the exact model name to pin.
4. **Speed promise (C1):** how should "under 5–10 seconds" be worded for Ollama?
5. **Simplifications:** apply O1 (single pending slot), O2, O3, O7, O8, O11?
6. **Fixes:** apply C2–C10?
7. **Row limit values:** 100 default / 1000 max, or something else?

---

## 7. Suggested build order

Each step can be tested alone before the next.

1. Fixtures (Chinook SQLite) and project skeleton
2. Provider interface + `FakeProvider`
3. Connection (SQLite first)
4. Schema Extraction
5. Query Validation (pure functions, easy to test hard)
6. Query Execution
7. Generate Query (with the real Gemini; prompt iteration starts here)
8. Pipeline function + API
9. Benchmark on SQLite (this is where prompt tuning gets measured)
10. UI
11. PostgreSQL/MySQL support and smoke tests
12. Ollama runs, write-up, demo video

Leaving the UI until after the benchmark is deliberate: the benchmark tells you whether the core works, and the UI is the part most likely to eat time without proving anything.
