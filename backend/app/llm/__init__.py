from app.llm.base import LLMProvider, LLMRequest, LLMResponse
from app.llm.errors import (
    ContextTooLong,
    LLMError,
    RateLimited,
    SetupProblem,
    Timeout,
    Unknown,
    Unreachable,
)
from app.llm.settings import load_settings

__all__ = [
    "LLMProvider", "LLMRequest", "LLMResponse", "get_provider",
    "LLMError", "SetupProblem", "Unreachable", "RateLimited",
    "Timeout", "ContextTooLong", "Unknown",
]


def get_provider(name: str | None = None) -> LLMProvider:
    """name = "gemini" | "ollama". If None, use the LLM_PROVIDER setting."""
    settings = load_settings()
    name = (name or settings.provider).strip().lower()

    if name == "ollama":
        from app.llm.ollama_provider import OllamaProvider
        return OllamaProvider(settings)
    if name == "gemini":
        from app.llm.gemini_provider import GeminiProvider
        return GeminiProvider(settings)
    raise SetupProblem(f"Unknown model provider: {name}")
