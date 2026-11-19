# Spec: Accuracy Benchmark

**Status:** Draft 1.1
**Depends on:** `intent.md` (success criteria), `spec-api.md` (the pipeline function, which takes plain values), `spec-query-execution.md`, `spec-llm-provider-interface.md`
**Used by:** README / portfolio write-up

---

## 1. Purpose

Measure how often the app gives the **correct answer**, separately for Gemini and Ollama, on two unrelated databases. The result is a real number for the portfolio, not a claim that "it works".

It also guards against the most dangerous failure named in `intent.md`: **a query that runs fine but answers the wrong question.**

The benchmark is a command-line script. It has no UI and no web server.

---

## 2. What Is Measured

For every question, the benchmark runs the same pipeline the app uses (`generate_validated_query` from the API spec, then `execute`). It then compares the result with the known-correct answer.

```
question --> pipeline --> run on fixture DB --> compare with reference result --> outcome
```

The **approval step is skipped**; the benchmark acts as an automatic approver. Everything else is identical to the app, so there is no special benchmark path.

---

## 3. Fixtures

| Fixture | Used for | Databases |
|---|---|---|
| **Chinook** (music store) | Main accuracy set, plus the PostgreSQL/MySQL smoke test | SQLite (main), PostgreSQL, MySQL |
| **Sakila** (DVD rental) | Main accuracy set; proves the app isn't tuned to one schema | SQLite |

- The **accuracy benchmark runs on SQLite only**, so it needs no database servers. This is the headline number.
- PostgreSQL and MySQL get a small **smoke test**: 2–3 of the Chinook questions, run once, to confirm the app connects and works on all three engines. It is a pass/fail check, not an accuracy score.
- Fixture files and setup scripts live in the repo (`fixtures/`), or the setup script downloads them. They are **read-only**.
- Sakila is originally a MySQL database; the SQLite version is a community port. Check that the port's data matches the original's row counts before relying on it.
- Test database connection details come from `.env` (not committed), with fake values in `.env.example`.

**No changes between fixtures.** The same code, prompt and settings are used for both. That is the domain-independence criterion from `intent.md`.

---

## 4. The Questions

About **4–5 answerable questions per fixture** (8–10 total), as set in `intent.md`. Each is stored in a file such as `benchmark/questions.yaml`:

```yaml
- id: chinook-03
  fixture: chinook
  question: "Which 5 artists have the most albums? Show the artist name and the number of albums."
  reference_sql: "SELECT ... "
  ordered: true          # row order matters (top-N). Default false.
```

Rules for writing them:
- **Cover a spread of difficulty:** one single-table filter, one join, one aggregate (`GROUP BY`), one multi-table join with aggregate, one with a date or a subquery.
- **Say exactly what to return** ("show the artist name and the number of albums"), so the correct result has one defined shape. Vague questions belong in the ambiguity set below.
- **Keep each expected answer under 100 rows**, so the validator's default `LIMIT` never changes the correct answer.
- The `reference_sql` is run on the same database to produce the expected result, so expected answers are never typed by hand.
- **Write all questions before the first run, and don't edit them to make results look better.** If a question turns out to be genuinely ambiguous, change it and record the change in the changelog. This is what makes the number honest.

### Ambiguity set (separate, small)
`intent.md` also requires that unclear questions are *not guessed at*. Add **1–2 questions per fixture** with an expected outcome of `clarification` or `not_found`:

```yaml
- id: chinook-A1
  question: "Who are our top customers?"
  expect: clarification     # by revenue? by number of orders?
- id: chinook-A2
  question: "What is the average customer age?"
  expect: not_found         # no such column in this database
```

These are reported separately and are **not part of the accuracy percentage**.

---

## 5. Comparing Results

A generated query is **correct** if its result matches the reference result:

1. Same number of columns.
2. Same rows, compared **by position of the column**, not by column name (aliases don't matter).
3. **Row order is ignored**, unless the question has `ordered: true`.
4. Numbers are compared with a tiny tolerance (`1e-6`); numeric text and numbers are treated alike (`"12.50"` equals `12.5`).
5. Text is compared exactly (no case or whitespace forgiveness).

Extra columns count as wrong. This is deliberate: the question says exactly what to return.

---

## 6. Outcomes

Every question ends in exactly one outcome:

| Outcome | Meaning |
|---|---|
| `correct` | Ran, and matches the reference |
| `wrong_result` | **Ran, but gave a different answer** (the silent-wrong-answer case) |
| `execution_error` | Passed validation, but the database returned an error |
| `rejected` | The validator blocked it |
| `clarification` | The model asked a question instead of answering |
| `not_found` | The model said something wasn't in the database |
| `model_error` | Provider error or unreadable reply |

**Accuracy = `correct` ÷ number of answerable questions.** Alongside it, `wrong_result` is always reported on its own, because it is the failure users can't see.

---

## 7. Running It

```
python -m benchmark.run --provider gemini --fixture all
python -m benchmark.run --provider ollama --fixture chinook
```

- Each question runs **once**. At temperature 0 results are mostly stable. If you want more confidence in the final numbers, run the whole benchmark again and keep both result files.
- A short pause between Gemini calls avoids rate limits. The provider's own capped retry (provider spec, section 4) still applies; a call that still fails is recorded as `model_error`, not retried again.
- Each question is independent: no memory between questions.

---

## 8. Output

Written to `benchmark/results/` (committed to the repo as evidence), one file per run:

- **Detail file (JSON):** per question — the question, generated SQL, outcome, result row count, time taken, error text if any.
- **Run header:** date, provider, **exact model name** (from the provider's response), temperature, `num_ctx` for Ollama, fixture names.
- **Summary table (Markdown),** for the README:

| Provider (model) | Fixture | Correct | Wrong result | Other failures | Accuracy |
|---|---|---|---|---|---|

The README table is the quotable portfolio number, with the model names and date shown beside it.

---

## 9. Testing the Benchmark Itself

- Use the `FakeProvider` to return a known-correct SQL, a known-wrong SQL, a bad SQL, a `CLARIFY:` and garbage, and check each lands in the right outcome.
- Check the comparison rules: reordered rows (ok unless `ordered`), renamed alias (ok), extra column (wrong), `12.5` vs `"12.50"` (ok), different text case (wrong).
- Check that a question's reference SQL actually runs and returns under 100 rows (a "lint" step before any model call).

---

## 10. Deferred (not in V1)

- More than ~10 questions, or an automatic question generator
- Comparing prompts or few-shot variants against each other
- Cost tracking per run
- A dashboard of results over time
- Larger or messier schemas (Phase 4)

---

## 11. Open Questions

1. **Exact Ollama model tag.** The headline local result uses Qwen 2.5 (the provider spec's default). Choose the size that runs well on your machine and pin the exact tag before the first run. DeepSeek R1 7B is an optional extra comparison.
2. **Gemini model string.** Default `gemini-2.5-flash`; confirm it is still available when you start.

---

## 12. Interview Note

**What this taught me:** "it runs" and "it's right" are different things. A query can run perfectly and answer the wrong question, so the benchmark compares the *results* with a reference answer, and reports wrong-but-running answers separately.

**What I'd say in an interview:** "I wrote the test questions before the first run and didn't tweak them afterwards, then measured Gemini and a local Ollama model separately. The headline result is a percentage, but the number I watched most was queries that ran and returned the wrong answer, since those fail silently for a real user."

---

## 13. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: fixtures, question format, ambiguity set, comparison rules, outcomes, output, testing |
| Draft 1.1 | Accuracy on SQLite only; PostgreSQL/MySQL reduced to a small smoke test; removed `--runs`; model defaults filled in |
| Draft 1.2 | Built: `benchmark/run.py`, `benchmark/compare.py`, questions in `benchmark/questions/<fixture>.yaml`, `benchmark/fixtures/download_sakila.py`. Ambiguity answers that write SQL are recorded as `guessed_sql` (not run). Two numeric texts (`"12.50"` and `"12.5"`) also count as equal. A lint step checks all reference SQL before any model call. `--lint-only` runs just that check. `--pause` sets seconds between questions (default 4 for Gemini). Chinook top-N questions use top 3 so ties cannot change the answer |
