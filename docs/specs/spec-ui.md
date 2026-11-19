# Spec: UI

**Status:** Draft 1
**Depends on:** `spec-api.md`
**Used by:** the demo / portfolio walkthrough

---

## 1. Purpose

A single, simple web page where the user connects a database, asks a question in plain English, reviews the generated SQL, approves it, and reads the result.

The UI contains **no SQL logic and no safety logic**. It only shows what the API returns and calls the API. The schema is **not displayed** (a decision in `intent.md`).

This spec describes screens and behaviour. The page is built as **plain HTML, CSS and JavaScript served by the same backend** (one file or a few, no build step, one thing to run). Only one page with a few areas is needed, so a frontend framework would add setup without adding value.

---

## 2. Layout

One page, four areas, top to bottom. Areas that don't apply yet are hidden or disabled.

```
┌──────────────────────────────────────────┐
│ 1. Connection        [connected: Chinook]│
├──────────────────────────────────────────┤
│ 2. Ask      [provider ▾]                 │
│    [ question box ........................]│
│    [Generate query]                      │
├──────────────────────────────────────────┤
│ 3. Message / Query preview               │
│    SQL (read-only)  · notes              │
│    [Approve & run]  [Discard]            │
├──────────────────────────────────────────┤
│ 4. Results table                         │
└──────────────────────────────────────────┘
```

---

## 3. Areas

### 3.1 Connection
- A **database type** selector: PostgreSQL, MySQL, SQLite.
- Fields change with the type:
  - PostgreSQL / MySQL: host, port (pre-filled with the default), database, username, password.
  - SQLite: file path only.
- Password field is a masked input, and is **cleared as soon as the connect request is sent**.
- **Connect** button. While connecting: "Connecting…" and the button is disabled.
- On success: the area collapses to a one-line summary — "Connected to PostgreSQL · 11 tables" — with **Disconnect** and **Refresh schema** buttons. Any schema warning (e.g. "34 tables; tested up to ~20") appears as a small notice.
- On failure: the API's friendly message appears under the form. The fields stay filled except the password.
- A short hint: "For safety, use a read-only database user."
- Credentials are **never saved** in the browser (no local storage, no cookies, no URL).

### 3.2 Ask
- **Provider** dropdown: Gemini or Ollama. Only providers reported as available by `/status` are selectable.
- A small line under the dropdown, changing with the choice: for Gemini, "Your question and the table/column names are sent to Google." For Ollama, "Everything stays on this computer."
- A multi-line **question box** and a **Generate query** button.
- Disabled until connected. Disabled with "Generating…" while waiting.
- Enter alone adds a new line; Ctrl+Enter (or the button) submits.

### 3.3 Message / Query preview
Shows exactly one of these, depending on the API's `status`:

| `status` | Shown |
|---|---|
| `ready` | The SQL in a read-only monospace box, notes from `changes` ("Added LIMIT 100"), and the buttons **Approve & run** and **Discard** |
| `clarification` | The question from the model, styled as a prompt: "Can you be more specific? …". The question box keeps its text so the user can edit it and generate again |
| `not_found` | "I can't find that in this database: …" |
| `rejected` | **No run button.** Wording depends on the `reason` code: `SYNTAX_ERROR` or `EMPTY` → "The model wrote SQL that isn't valid for this database. Try rephrasing."; any safety reason (`NOT_READ_ONLY`, `MULTIPLE_STATEMENTS`, `FORBIDDEN_CLAUSE`, `FORBIDDEN_FUNCTION`) → "This query was blocked because it isn't a safe read-only query." |
| `error` | The friendly error message, with a "Try again" hint |

The SQL box is **not editable** in V1 (editing would need re-validation; see the validation spec).

### 3.4 Results
- Shown after **Approve & run**. While running: "Running…", and Approve is disabled so it can't be pressed twice.
- A table with column headers and rows. `NULL` shows as a faint "NULL".
- A line under the table: "42 rows · 120 ms". If the row count equals the limit, add "Showing the first N rows."
- Zero rows: "No rows returned."
- Long tables scroll inside the table area; headers stay visible.
- Execution errors (timeout, bad column) appear in the message area with the API's message; the SQL stays visible.

---

## 4. States

```
disconnected --connect--> connected
connected --generate--> generating --> (ready | clarification | not_found | rejected | error)
ready --approve--> running --> (results | execution error)
ready --discard--> connected
any --disconnect--> disconnected
```

- A new **Generate** clears the previous preview and results.
- **Discard** removes the pending query (the page forgets its `query_id`).
- If the API says the query is no longer available, show "This query is no longer available. Please generate it again."
- On page reload, the page asks `/status`: if the backend is still connected it shows the connected state (no password is needed again); otherwise the connection form.

---

## 5. Behaviour Rules

- The page **never sends SQL** to the server. Only the question, and later the `query_id` (API spec, section 6).
- One request at a time: while one is in flight, the other action buttons are disabled.
- Clarification is **single-shot**: the user rewrites the question; there is no chat history in V1.
- Every failure shows a message. No silent failures, no raw error text or stack traces.
- Plain, readable defaults: clear labels, sufficient contrast, usable by keyboard (tab order follows the layout). Desktop-first; mobile layout is not a V1 goal.

---

## 6. Testing

- Manual walkthrough of the happy path on Chinook and Sakila (also the demo script).
- Each `status` is shown correctly (tests use the fake model through the API).
- **Approve cannot be double-clicked** into two runs.
- Password field is empty after connect; nothing sensitive in browser storage (check in dev tools).
- Disconnect clears the preview and results.
- Keep the status-to-message mapping in one small function so it can be tested on its own.

---

## 7. Deferred (not in V1)

- Showing the schema (decided against)
- Editing the SQL before approving
- Query history, saved queries, example-question chips
- Export results (CSV)
- Dark mode, mobile layout, accessibility audit beyond the basics
- Chat-style follow-ups (stretch goal in `intent.md`)

---

## 8. Open Questions

1. **Syntax highlighting** for the SQL box. Nice for the demo, cheap with a small library, not required.

---

## 9. Interview Note

**What this taught me:** deciding what the UI does *not* do. It has no SQL logic and no safety logic, so a bug in the page can't weaken the safety of the system.

**What I'd say in an interview:** "The interface only displays what the server returns and sends back an ID to approve. The approve button is the only thing that can start a query, the SQL box is read-only, and when the model is unsure the page shows its clarifying question instead of guessing."

---

## 10. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: layout, areas, states, behaviour rules, testing |
| Draft 1.1 | Frontend decided: plain HTML/JS served by the backend; rejected-query wording by reason; provider privacy line; matches single pending-query slot |
