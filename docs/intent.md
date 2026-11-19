# Text-to-SQL App — Intent Document

## Executive Summary
An AI-powered, plug-and-play text-to-SQL web app. A user connects any SQL database (PostgreSQL/MySQL/SQLite), the app parses the schema automatically, and the user can ask questions in plain English. An LLM generates the SQL query, the user reviews and approves it, and only then does it execute — safely, with guardrails. This is a portfolio/learning project built to demonstrate applied AI integration and disciplined engineering practice (Spec-Driven Development) using Claude Code.

> **Note on tooling vs. product**: Claude Code is the *development tool* used to build this project. The *deployed app itself* generates SQL using Gemini or Ollama (see "LLM Provider" below) — these are separate decisions.

## Problem
Non-technical users (and even technical ones in a hurry) often need quick answers from a database but don't know SQL or don't want to write it by hand. Pasting schema into a chat tool and copy-pasting SQL back into a DB client is slow, manual, and risky (no validation, no safety net against a bad or destructive query).

## Solution
A web app that:
1. Connects directly to the user's live database and extracts the schema automatically — no manual copy-pasting.
2. Takes a natural language question and generates a SQL query using an LLM (Gemini or Ollama), with the correct schema context.
3. Shows the generated query for review before anything runs — never executes blindly.
4. Validates and restricts execution (read-only, safe limits) so a bad generation can't cause damage.

## Target User
Primarily a demonstration for recruiters/hiring managers and other developers viewing this as a portfolio piece — but designed as if for a real non-technical business user (e.g., a founder, analyst, or PM) who needs quick answers from a database without SQL knowledge.

## Why This, Not Just "Ask Claude/ChatGPT for SQL"
- **Direct DB connection**: no manual schema copy-pasting — the app reads the live schema itself.
- **Safety by design**: validation, read-only enforcement, and an explicit approval step before execution — a chat tool can't do this.
- **Purpose-built UX**: a simple, focused interface made for this one job, rather than a general-purpose chat window.

## LLM Provider
The app supports **both Gemini (cloud API) and Ollama (local model)** for SQL generation — the user picks which one to use. This is a deliberate design choice, not indecision:
- **Gemini**: better expected accuracy, requires an API key and internet, has usage costs. The table/column names and the user's question are sent to Google, so the UI says so next to the provider choice.
- **Ollama**: fully local and free, more private (schema/queries never leave the machine), but likely weaker SQL generation accuracy.
- Supporting both means the Generate Query component must be built behind a **provider-agnostic interface** (same prompt-building logic, swappable backend) — this is itself a clean, specable piece of architecture and a good interview talking point ("designed for pluggable LLM backends").
- The accuracy benchmark (see Success Criteria) will be run against **both** providers so the tradeoff is backed by real numbers, not assumption.

## Personal Goals for This Project
Beyond the app itself, this project is a vehicle to learn and demonstrate:
- Practical integration of an LLM into a real application (prompt design, schema-aware generation, error handling) — including designing for more than one provider.
- Spec-Driven Development as a working method — writing specs first, building to them, testing against them.
- Using Claude Code effectively as a development partner on a non-trivial project.

### Portfolio & Career Objective (primary driver)
The underlying goal of this project — and future projects built this way — is to produce strong, defensible resume/portfolio material for job applications (India and international/remote roles). This shapes decisions throughout, not just the writeup at the end:
- Prefer choices that make a good **interview story** (a real security decision, a real tradeoff, a bug that taught something) over choices that are merely fast.
- Each spec should be able to answer: *"What did building this teach me, and what would I say about it in an interview?"*
- The finished app should be **demoable** (screen recording / live link) and the **specs + commit history** should be clean enough to show as evidence of process, not just output.
- This objective stays between us as the lens we evaluate decisions through — the project itself is presented publicly on its own merits (a genuinely useful, well-built tool), not framed around job-hunting.

## Success Criteria (V1)
- [ ] Can connect to a PostgreSQL, MySQL, or SQLite database by entering credentials.
- [ ] Schema is extracted and used automatically — no manual input required.
- [ ] A plain-English question produces a syntactically valid SQL query, typically within ~10 seconds with Gemini. Ollama's speed depends on the user's hardware, so it is measured and reported, not promised.
- [ ] Generated query is shown to the user before execution; nothing runs without approval.
- [ ] Destructive queries (DELETE/UPDATE/DROP/etc.) are never executed.
- [ ] Results are displayed in a readable table.
- [ ] The whole thing works end-to-end as a demoable product (e.g., for a portfolio walkthrough or video).
- [ ] Built using documented specs (kept in `/docs` once the repo is created) that match what was actually built.
- [ ] **Accuracy benchmark**: a fixed set of 4–5 natural language test questions per database fixture (so ~8–10 total across Chinook + Sakila) is run against the app, and the generated SQL's results are checked against known-correct answers. Accuracy (% correct) is measured and reported **separately for Gemini and Ollama**, and documented in the repo — this becomes a real, quotable number for the portfolio, not just a claim that "it works."
- [ ] **Ambiguous queries are handled gracefully**: when a question can't be confidently mapped to the schema (missing info, multiple valid interpretations), the app asks a clarifying question instead of guessing — see "Ambiguous Query Policy" below.

## Ambiguous Query Policy
When a user's question can't be confidently turned into one correct SQL query, the app must **not silently guess**. Policy for V1:
- If a question references something not in the schema (e.g., a column that doesn't exist) → respond with a clear "I can't find X in this database" message, don't fabricate a column.
- If a question has multiple reasonable interpretations (e.g., "top customers" — by revenue? by order count?) → ask a short clarifying question back to the user instead of picking one silently.
- If the LLM itself expresses low confidence or returns something that isn't valid SQL → surface that to the user rather than trying to run it anyway.
This policy directly shapes the Generate Query spec (it needs a defined way to signal "clarification needed" vs. "here's the SQL") and is one of the more interesting engineering problems in the project — worth calling out explicitly rather than leaving implicit.

## Scope

### In Scope (V1)
- Connect to one database at a time (PostgreSQL, MySQL, SQLite).
- Automatic schema extraction (tables, columns, types, keys, relationships).
- Natural language → SQL generation via Gemini or Ollama (user-selectable), with schema-aware prompting.
- Query validation: SELECT-only, auto-added LIMIT, basic syntax checks.
- User approval step before execution.
- Query execution with timeout and row limits.
- Simple, clean single-user UI: input box, query preview, results table.
- Basic error handling and feedback (e.g., failed connection, invalid query).

### Out of Scope (Future Phases)
- Multi-user accounts, authentication, and permissions (Phase 3).
- Connection pooling and performance optimization for scale (Phase 2–3).
- Query cost estimation / EXPLAIN-based analysis (Phase 4).
- Row-level security / data masking (Phase 3+).
- Support for additional database engines beyond the initial three (later).
- Conversational follow-up refinement ("now filter by X") — V1 is single-shot per query (stretch goal, not committed).

## Constraints
- **Solo developer**, building with Claude Code (Pro).
- **Timeline**: aiming for roughly 1 month, not a hard deadline — optimizing for learning and a solid finished demo over speed.
- No dedicated budget for infrastructure beyond free/low-cost hosting and Gemini API usage (Ollama runs free/local).

## Assumptions
- The user provides valid database credentials with at least read access.
- Target databases follow standard SQL conventions (no exotic/proprietary dialects beyond Postgres/MySQL/SQLite differences).
- LLM provider (Gemini or Ollama) availability and latency are acceptable for an interactive (not real-time) tool.
- Demo/testing will primarily use sample databases, not sensitive production data.
- Schema size for V1 is assumed to be modest — tested and expected to work well up to roughly **20 tables**. Larger schemas (needing smart filtering of what's sent to the LLM) are a Phase 2+ concern, not V1.

## Risks
Named here so later specs account for them, not just discovered mid-build:
- **Silent wrong answers**: SQL that runs successfully but answers the wrong question is more dangerous than SQL that fails outright, since nothing flags it. Mitigation: the accuracy benchmark above exists specifically to catch this, not just "does it run."
- **Schema drift**: if the database changes after its schema was read/cached, the app may generate queries against tables/columns that no longer exist. V1 has no caching yet, so this is a Phase 2 risk, but worth tracking now.
- **Prompt injection via schema**: unusual table/column names could theoretically be crafted to manipulate the LLM's behavior. Low risk for V1 (self-controlled test databases), but worth a mention since it's a real class of problem in production text-to-SQL tools.
- **Cost/rate limits during testing**: repeated benchmark runs against Gemini could hit usage costs or rate limits — Ollama testing has no such limit, which is itself a point of comparison.

## Credential Handling (V1)
Even though V1 runs locally only, database credentials are real and must be handled deliberately, not carelessly:
- Credentials are **never logged** (not to console, not to any log file).
- Credentials are **never committed** to the repo — `.env` or equivalent, and `.gitignore`'d.
- Credentials are kept **in memory only for the active session** and are not persisted to disk between runs.
- This stance carries forward and gets stricter when the app is later deployed publicly (its own spec, per the Deployment Plan above).

## Roadmap
- **Phase 1 (V1 — this project's focus)**: Working end-to-end MVP — connect, extract schema, generate, validate, approve, execute, display results.
- **Phase 2**: Robustness — schema caching, smarter validation, error-feedback retry loop with the LLM, audit logging.
- **Phase 3**: Multi-user support, connection pooling, basic auth.
- **Phase 4**: Advanced features — schema embedding/similarity search for large DBs, query cost estimation, conversational refinement.

## Deployment Plan
- **V1**: built and tested locally only. No hosting, no public credential handling yet — keeps the first build focused purely on getting the core pipeline (connect → schema → generate → validate → execute) solid and well-specced.
- **After V1 is working and demoed locally**: deploy it publicly (live link), which becomes its own spec'd phase (hosting choice, credential security for a public-facing app, HTTPS, rate limiting on Gemini API calls, hosting considerations for Ollama if offered publicly, etc.) — this later phase is itself good CV material ("took a local prototype to a secured public deployment").

## Test Fixtures (Domain Independence)
The app itself is domain-agnostic by design — it works off whatever schema it extracts, with no domain-specific logic. To prove that (not just claim it), V1 will be tested against at least two unrelated sample databases:
- **Chinook** (music store: artists, albums, tracks, invoices, customers) — available in SQLite, PostgreSQL, and MySQL versions, so it doubles as the cross-dialect test fixture.
- **Sakila / Pagila** (DVD rental: films, actors, rentals, stores) — structurally and semantically unrelated to Chinook, used to prove the app isn't implicitly tuned to one schema shape.

**Success criterion addition**: the app produces correct results on both fixtures without any code or prompt changes between them.

## Document Changelog
| Date | Change |
|---|---|
| Draft 1 | Initial intent.md: vision, SDD approach, scope, roadmap |
| Draft 2 | Added portfolio/career objective as evaluation lens; locked deployment plan (local-first, deploy after V1) |
| Draft 3 | Added test fixtures (Chinook + Sakila) for domain-independence verification |
| Draft 4 | Added accuracy benchmark criterion, ambiguous query policy, risks, credential handling stance, scale assumption; switched LLM provider from Claude API to user-selectable Gemini/Ollama with provider-agnostic design |
| Draft 5 | Schema success criterion changed from "displayed/used" to "used" — schema is not shown in the UI in V1 |
| Draft 6 | Speed goal reworded (Gemini typical, Ollama reported); Gemini privacy note; specs location wording |
