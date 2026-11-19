import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

from app.llm.errors import SetupProblem

load_dotenv()  # reads .env if present; real environment variables win


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    temperature: float
    timeout_seconds: float
    gemini_api_key: str | None = field(repr=False)  # secret: hidden from repr
    gemini_model: str
    ollama_base_url: str
    ollama_model: str
    ollama_num_ctx: int


def _number(name: str, default: str, cast):
    raw = os.environ.get(name, default)
    try:
        return cast(raw)
    except ValueError:
        raise SetupProblem(f"Setting {name} must be a number.") from None


def load_settings() -> LLMSettings:
    """Read settings from the environment each time (so tests can change them)."""
    return LLMSettings(
        provider=os.environ.get("LLM_PROVIDER", "gemini").strip().lower(),
        temperature=_number("LLM_TEMPERATURE", "0", float),
        timeout_seconds=_number("LLM_TIMEOUT_SECONDS", "30", float),
        gemini_api_key=os.environ.get("GEMINI_API_KEY") or None,
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
        ollama_base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/"),
        ollama_model=os.environ.get("OLLAMA_MODEL", "qwen2.5:7b"),
        ollama_num_ctx=_number("OLLAMA_NUM_CTX", "8192", int),
    )
