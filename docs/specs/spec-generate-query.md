# Spec: Generate Query

**Status:** Draft 1
**Depends on:** `spec-llm-provider-interface.md`, `spec-schema-extraction.md` (supplies `schema_text`)
**Used by:** `spec-query-validation.md`, `spec-benchmark.md`

---

## 1. Purpose

Generate Query turns a plain-English question into SQL. It owns:

- Building the prompt (system instructions + few-shot examples + schema + question)
- Deciding the outcome: **SQL**, **needs clarification**, or **not found**
- Reading the model's reply and handling messy output

It does **not** call Gemini/Ollama directly — it hands the prompt to the **Provider Interface** and gets text back.

```
question + schema + db_type
        |
        v
  Generate Query (builds prompt)
        |
        v
  Provider.generate()  -->  Gemini / Ollama
        |
        v
  Generate Query (reads reply)
        |
        v
  one of: SQL | clarification | not_found
```

---

## 2. Input

```python
@dataclass
class GenerateQueryInput:
    question: str       # the user's plain-English question
    schema_text: str     # table/column/type/key info (from schema extraction)
    db_type: str          # "postgresql" | "mysql" | "sqlite"
```

`db_type` matters because SQL dialects differ slightly (e.g. `LIMIT` syntax, quoting rules).

The function is `generate_query(input: GenerateQueryInput, provider: LLMProvider) -> GenerateQueryResult`. The provider is passed in, so tests can pass a `FakeProvider` and the app can pass whichever one the user chose.

---

## 3. Reply Format (plain SQL + marker)

We do **not** use JSON (see section 8 for why). The model replies with plain text in one of three shapes:

| Model writes | We treat it as |
|---|---|
| `SELECT ...` or `WITH ...` (starts with SQL) | **sql** — hand it to Query Validation |
| `CLARIFY: <question to ask the user>` | **clarification** — show the message, no SQL runs |
| `NOT_FOUND: <what's missing>` | **not_found** — show the message, no SQL runs |
| Anything else / empty / broken | **error** — see section 6 |

This is chosen because it's simple to check (just look at the first line) and doesn't depend on the model producing valid JSON, which small/local models aren't always reliable at.

---

## 4. Reading the Reply

Steps, in order:

1. **Strip `<think>...</think>`.** Some models (e.g. DeepSeek R1) write out their reasoning first, wrapped in these tags. Remove everything inside them before reading further. This is a documented quirk of reasoning models, not a general rule.
2. **Trim whitespace and markdown fences** (` ```sql ... ``` `, stray backticks) — small models sometimes wrap answers in code blocks.
3. **Check the first line:**
   - Starts with `CLARIFY:` → outcome = clarification, message = rest of line
   - Starts with `NOT_FOUND:` → outcome = not_found, message = rest of line
   - Starts with `SELECT` or `WITH` (case-insensitive) → outcome = sql, sql = full text. (`WITH` is allowed because queries using a CTE start with it. This step only decides "does this look like SQL"; whether it is safe is decided by Query Validation.)
   - Anything else → outcome = error (section 6)

```python
@dataclass
class GenerateQueryResult:
    status: str          # "sql" | "clarification" | "not_found" | "error"
    sql: str | None = None
    message: str | None = None
```

---

## 5. Prompt Contents

The prompt sent to the provider has four parts, in this order. Parts 1–2 go in the provider's `system_prompt`; parts 3–4 go in its `user_prompt`.

1. **System instructions** — explain the job, the three possible reply shapes, and the safety rule below.
2. **Few-shot examples** — 2–3 short examples using a tiny made-up 2-table schema (not Chinook/Sakila — using the real test schemas here would make the benchmark unfair). One example each for `sql`, `CLARIFY:`, and `NOT_FOUND:`.
3. **Schema text** — the real schema for the connected database.
4. **The user's question.**

**Safety line (prompt injection risk from intent.md):** the system instructions explicitly state that the schema (table/column names) is *data describing the database*, never instructions to follow. This stops a maliciously-named table or column from trying to change the model's behavior.

**Token discipline:** examples are short, fixed text with no sample data rows, and schema text includes only names/types/keys — no real example rows either. This keeps prompt size (and cost) predictable, which matters for the benchmark and for Ollama's context window.

---

## 6. Bad / Unreadable Replies

If the reply doesn't match any of the three known shapes (section 3), or the provider itself raised an error (`LLMError` from the provider spec):

- Outcome = `error`, with a plain message like "Couldn't understand the model's reply."
- **No automatic retry in V1.** The user sees the error and can rephrase or try again manually.
- This is never shown as a silent failure — something always reaches the user (this directly implements the "ambiguous/low-confidence" part of intent.md's Ambiguous Query Policy).

---

## 7. Models

| Role | Model | Note |
|---|---|---|
| Primary (demo) | Gemini 2.5 Flash | Reliable at following the reply format |
| Secondary (local) | Qwen 2.5 (Ollama) | Preferred local model — more consistent plain-text output |
| Secondary, alternative | DeepSeek R1 7B (Ollama) | Usable, but needs the `<think>` stripping step (section 4) since it's a reasoning model |

Both secondary options go through the same code path — the `<think>` stripping step runs for every reply and simply does nothing if there's no `<think>` block.

---

## 8. Why Not JSON

Considered and rejected for V1:

- Gemini 2.5 Flash would handle JSON reliably (it has a native JSON mode).
- Small/local Ollama models are less consistent at it, and DeepSeek R1 specifically tends to leak reasoning text around strict formats.
- Since the benchmark compares Gemini and Ollama fairly, the reply format needs to be equally easy for both — plain SQL with a marker line is simpler to produce correctly than valid JSON, for any model size.

---

## 9. Testing

- Uses the **FakeProvider** from the provider spec to test reply-reading logic without calling a real model: valid SQL, `CLARIFY:`, `NOT_FOUND:`, broken text, text with a `<think>` block, text wrapped in code fences.
- Prompt-building is tested separately from reply-reading (two small, independent units).
- Real Gemini/Ollama runs are exercised through the accuracy benchmark, not unit tests.

---

## 10. Deferred (not in V1)

- Conversational follow-ups ("now filter by X") — intent.md marks this as a stretch goal only.
- Automatic retry with error feedback to the model (Phase 2).
- JSON/structured output mode (could revisit later if local models improve).

---

## 11. Interview Note

**What this taught me:** matching the output format to the weakest model in the system, not the strongest, mattered more than I expected — a format that's "better" (JSON) can actually produce an unfair benchmark if one provider struggles to produce it reliably.

**What I'd say in an interview:** "I chose a plain-text reply format with simple markers instead of JSON because I was comparing a cloud model against small local models, and I didn't want the comparison to really be measuring JSON formatting ability. I also had to handle reasoning-model output (DeepSeek R1) differently, since it writes out its thinking before the actual answer."

---

## 12. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: input/output, plain-SQL-marker format, think-tag stripping, few-shot token discipline, model choices |
| Draft 1.1 | Reply may start with `SELECT` or `WITH`; dependency lines now point to the schema, validation and benchmark specs |
| Draft 1.2 | Provider is passed in as a parameter; stated which prompt parts go in system vs user prompt |
| Draft 1.3 | Built in `backend/app/generate/`. Markers are matched case-insensitively; an empty `CLARIFY:`/`NOT_FOUND:` is an error; provider errors reuse the provider's own friendly message |
