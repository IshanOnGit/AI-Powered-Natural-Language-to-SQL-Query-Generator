import time
from dataclasses import dataclass
from typing import Callable, Protocol

from app.llm.errors import RateLimited, Unreachable

# Only these are temporary. Everything else would fail the same way again.
RETRYABLE = (Unreachable, RateLimited)
MAX_ATTEMPTS = 2        # first try + 1 retry, never more
RETRY_WAIT_SECONDS = 2.0


@dataclass
class LLMRequest:
    system_prompt: str
    user_prompt: str


@dataclass
class LLMResponse:
    text: str        # what the model returned
    model: str       # model actually used (for benchmark records)
    provider: str    # "gemini" or "ollama"


class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse:
        """Return the model's reply, or raise an LLMError."""
        ...

    def is_available(self) -> bool:
        """Cheap check. Never raises."""
        ...


class BaseProvider:
    """Shared retry logic. A real provider only writes `_generate_once`."""

    name = "base"

    def __init__(self, sleep: Callable[[float], None] = time.sleep):
        self._sleep = sleep

    def generate(self, request: LLMRequest) -> LLMResponse:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                return self._generate_once(request)
            except RETRYABLE:
                if attempt == MAX_ATTEMPTS:
                    raise
                self._sleep(RETRY_WAIT_SECONDS)
        raise AssertionError("unreachable")  # loop always returns or raises

    def _generate_once(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError

    def is_available(self) -> bool:
        raise NotImplementedError
