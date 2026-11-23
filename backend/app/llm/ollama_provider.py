import httpx
from ollama import ResponseError

from app.llm.base import BaseProvider, LLMRequest, LLMResponse
from app.llm.errors import (
    ContextTooLong,
    LLMError,
    RateLimited,
    SetupProblem,
    Timeout,
    Unknown,
    Unreachable,
)
from app.llm.settings import LLMSettings, load_settings



class OllamaProvider(BaseProvider):
    """Local model through Ollama, using LangChain."""

    name = "ollama"

    def __init__(self, settings: LLMSettings | None = None, **kwargs):
        super().__init__(**kwargs)
        self._settings = settings or load_settings()

    def _make_client(self):
        from langchain_ollama import ChatOllama

        s = self._settings
        return ChatOllama(
            model=s.ollama_model,
            base_url=s.ollama_base_url,
            temperature=s.temperature,
            num_ctx=s.ollama_num_ctx,  # set on purpose: Ollama cuts long prompts silently
            client_kwargs={"timeout": s.timeout_seconds},
        )

    def _generate_once(self, request: LLMRequest) -> LLMResponse:
        from langchain_core.messages import HumanMessage, SystemMessage

        s = self._settings
        try:
            reply = self._make_client().invoke(
                [
                    SystemMessage(content=request.system_prompt),
                    HumanMessage(content=request.user_prompt),
                ]
            )
        except LLMError:
            raise
        except Exception as exc:
            raise _translate(exc, s) from None

        # If the prompt filled the whole context window, Ollama probably
        # dropped the start of it. Say so instead of returning a bad answer.
        used = (reply.response_metadata or {}).get("prompt_eval_count")
        if used is not None and used >= s.ollama_num_ctx:
            raise ContextTooLong()

        return LLMResponse(
            text=str(reply.content),
            model=s.ollama_model,
            provider=self.name,
        )

    def is_available(self) -> bool:
        """True if the Ollama server answers. Never raises."""
        try:
            r = httpx.get(f"{self._settings.ollama_base_url}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False


def _translate(exc: Exception, s: LLMSettings) -> LLMError:
    """Turn a library error into one of our simple errors."""
    if isinstance(exc, (httpx.TimeoutException, TimeoutError)):
        return Timeout()
    if isinstance(exc, (httpx.ConnectError, ConnectionError)):
        return Unreachable("Ollama isn't running. Start it and try again.")
    if isinstance(exc, ResponseError):
        status = getattr(exc, "status_code", None)
        text = str(exc).lower()
        if status == 404:
            return SetupProblem(
                f"Model not found. Run `ollama pull {s.ollama_model}` and try again."
            )
        if status == 429:
            return RateLimited()
        if status == 503:
            return Unreachable("Ollama server is temporarily unavailable.")
        if "context" in text or "too long" in text:
            return ContextTooLong()
    return Unknown()
