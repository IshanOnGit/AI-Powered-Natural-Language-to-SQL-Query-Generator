# AGENTS.md

Instructions for AI coding tools working in this repo.

## How we work (spec-driven)
- Read `docs/intent.md` and the relevant file in `docs/specs/` before writing code.
- Build in the order in `docs/reviews/spec-review.md` (section 7). One step at a time.
- If the code must differ from a spec, update the spec in the same change.
- Only build what the user asks for in the current step. Do not write later steps early.
- Keep answers and code simple and easy to understand.

## Layout
- `backend/app/` – application code, one module per spec
- `backend/tests/` – tests, mirroring `backend/app/`
- `frontend/` – plain HTML/JS, no framework, no build step
- `benchmark/` – fixtures, questions, results

## Safety rules (do not weaken)
- Never read, print or log `.env` or any API key.
- The browser never sends SQL to be run, only a query ID.
- Generated SQL must pass validation and the database connection must stay read-only.

## Commands
From the project root:

```
python -m venv .venv
.venv\Scripts\activate          (Windows)   or   source .venv/bin/activate
pip install -r requirements.txt
python benchmark/fixtures/download_chinook.py
pytest
cd backend && uvicorn app.main:app --reload
```
