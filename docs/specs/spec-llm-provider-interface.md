# Spec: LLM Provider Interface

**Status:** Draft 1
**Depends on:** `intent.md`
**Used by:** Generate Query spec, accuracy benchmark

---

## 1. Purpose

The app can use **Gemini** (cloud) or **Ollama** (local) to write SQL. The rest of the app should not care which one is used.

The Provider Interface is a small wrapper around LangChain that does one job:

> Take a prompt, send it to the chosen model, return the text (or a clear error).

### What it does NOT do
- It does not build prompts or know about schemas or SQL. That is **Generate Query's** job.
- It does not decide "SQL / clarify / not found". That is also **Generate Query's** job.

```
Generate Query  --prompt-->  Provider Interface  --LangChain-->  Gemini or Ollama
                <--text/error--
```

---

## 2. The Interface

One method. No streaming. No tool calling.

```python
from dataclasses import dataclass
from typing import Protocol

@dataclass
class LLMRequest:
    system_prompt: str
    user_prompt: str

@dataclass
class LLMResponse:
    text: str            # what the model returned
    model: str           # model actually used (for benchmark records)
    provider: str        # "gemini" or "ollama"

class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Return the model's reply, or raise an LLMError."""
        ...

    def is_available(self) -> bool:
        """Cheap check: is the key set (Gemini) / is the server reachable (Ollama)?
        Never raises. Used by the UI to show which providers can be chosen."""
        ...

def get_provider(name: str | None = None) -> LLMProvider:
    """name = "gemini" | "ollama". If None, use the LLM_PROVIDER setting."""
```

The user picks the provider in the UI for each question, so `get_provider(name)` is how the choice reaches the code. `LLM_PROVIDER` is only the default.

Temperature, timeout, and model name are **not** part of each request. They come from settings (section 5), so every call in a benchmark run uses the same values.

Two real providers implement it: `GeminiProvider` and `OllamaProvider`. Both use LangChain inside.

---

## 3. Errors

Providers never leak raw LangChain/Gemini/Ollama errors. They convert them into a few simple ones:

| Error | Meaning | Example message for the user |
|---|---|---|
| `SetupProblem` | API key missing, or model name wrong / not downloaded | "Gemini API key is not set. Check your .env file." / "Model not found. For Ollama, run `ollama pull <model>`." |
| `Unreachable` | Can't connect | "Can't reach Gemini. Check your internet." / "Ollama isn't running. Start it." |
| `RateLimited` | Too many requests / quota hit | "Too many requests. Wait a moment and try again." |
| `Timeout` | No answer within the time limit | "The model took too long to answer." |
| `ContextTooLong` | Prompt is bigger than the model can take | "The schema is too large for this model." |
| `Unknown` | Anything else (includes Gemini's safety filter refusing) | "Something went wrong with the model." |

All of them share one base class, `LLMError`, so other code can catch just that. `LLMError` is a subclass of the project-wide `AppError(code, message)` (see `spec-api.md`, section 7). Codes are prefixed, e.g. `llm_timeout`, `llm_setup_problem`.

---

## 4. Timeouts and Retries (no endless hitting)

1. **Timeout on every call.** Default 30 seconds. After that, stop and raise `Timeout`.
2. **Maximum 2 attempts total** (the first try + 1 retry). Never more, so there is no infinite loop.
3. **Retry only temporary errors:** `Unreachable` and `RateLimited`. Wait 2 seconds before the retry.
4. **Never retry** `SetupProblem`, `ContextTooLong`, `Timeout` or `Unknown`. Trying again gives the same result (and a timeout has already waited long enough).
5. After the last failure, raise the error. The UI shows the friendly message.

---

## 5. Settings

All read from environment variables (loaded from `.env`).

| Setting | Used by | Default | Notes |
|---|---|---|---|
| `LLM_PROVIDER` | both | `gemini` | Default only; the user's choice in the UI overrides it |
| `LLM_TEMPERATURE` | both | `0` | Keep 0 for the benchmark |
| `LLM_TIMEOUT_SECONDS` | both | `30` | |
| `GEMINI_API_KEY` | Gemini | none | Required for Gemini. Secret. |
| `GEMINI_MODEL` | Gemini | `gemini-2.5-flash` | Pinned; recorded in benchmark results |
| `OLLAMA_BASE_URL` | Ollama | `http://localhost:11434` | |
| `OLLAMA_MODEL` | Ollama | a Qwen 2.5 model (size chosen to fit your hardware) | Pin the exact tag before the first benchmark; recorded in results |
| `OLLAMA_NUM_CTX` | Ollama | set explicitly (choose per model) | See below |

**Why `OLLAMA_NUM_CTX` matters:** Ollama can silently cut off a long prompt if its context size is too small. A 20-table schema may be long enough for this to happen. If it does, Ollama's benchmark score would reflect a settings mistake, not the model's real ability. So we always set this value on purpose.

---

## 6. Secrets (API key safety)

- The real key lives **only** in `.env`, which is listed in `.gitignore`.
- The repo contains `.env.example` with fake placeholder values only.
- Claude Code is blocked from reading `.env` through its permission settings, so the real key never enters its context.
- The code **never prints or logs** the key, including inside error messages.
- The real key is never pasted into chat.

---

## 7. Testing

### Fake provider
A `FakeProvider` that returns canned replies or raises chosen errors. Used to test Generate Query without calling a real model.

### Shared test set (conformance tests)
Every provider (Fake, Gemini, Ollama) must pass the same tests:
1. A normal call returns text, model name, and provider name.
2. Missing config raises `SetupProblem`.
3. Unreachable service raises `Unreachable`.
4. Slow response raises `Timeout`.
5. Retry happens once for temporary errors and never for the others.
6. The API key never appears in any error message.

Real Gemini/Ollama tests are marked as "integration tests" and run only when a key or local server is available.

### Benchmark
The accuracy benchmark uses this same interface. There is no special path for it.

---

## 8. Deferred (not in V1)

- Streaming responses
- Tool / function calling
- A "capabilities" list per provider
- Smart retry with error feedback to the model (Phase 2)
- More providers (OpenAI, Claude, etc.)

---

## 9. Interview Note

**What this taught me:** Depending directly on a library makes code hard to test and hard to change. By putting a small interface around LangChain, I could swap models, fake them in tests, and give users clear errors.

**What I'd say in an interview:** "I wrapped LangChain behind my own provider interface so the app doesn't depend on it. It has timeouts and a capped retry, so a flaky API can't cause an infinite loop, and the API key is kept out of logs and out of my AI coding tool's context."

---

## 10. Changelog

| Date | Change |
|---|---|
| Draft 1 | Initial spec: interface, errors, timeouts/retries, settings, secrets, testing |
| Draft 1.1 | Added `get_provider(name)` and `is_available()`; reduced errors from 8 to 6; pinned default models; errors now build on shared `AppError` |
