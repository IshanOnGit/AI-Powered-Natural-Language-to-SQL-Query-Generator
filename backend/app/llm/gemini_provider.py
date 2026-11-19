import httpx

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

NO_KEY_MESSAGE = "Gemini API key is not set. Check your .env file."
BAD_KEY_MESSAGE = "Gemini rejected the API key. Check your .env file."


class GeminiProvider(BaseProvider):
    """Cloud model through Gemini, using LangChain."""

    name = "gemini"

    def __init__(self, settings: LLMSettings | None = None, **kwargs):
        super().__init__(**kwargs)
        self._settings = settings or load_settings()

    def _make_client(self):
        from langchain_google_genai import ChatGoogleGenerativeAI

        s = self._settings
        return ChatGoogleGenerativeAI(
            model=s.gemini_model,
            google_api_key=s.gemini_api_key,
            temperature=s.temperature,
            timeout=s.timeout_seconds,
            max_retries=0,  # our own retry rule lives in BaseProvider
        )

    def _generate_once(self, request: LLMRequest) -> LLMResponse:
        from langchain_core.messages import HumanMessage, SystemMessage

        s = self._settings
        if not s.gemini_api_key:
            raise SetupProblem(NO_KEY_MESSAGE)

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
            # "from None": the library's error text can contain the API key.
            raise _translate(exc, s) from None

        text = _reply_text(reply)
        if not text.strip():
            # Usually Gemini's safety filter refusing to answer.
            raise Unknown("Gemini did not return an answer (it may have refused).")

        return LLMResponse(text=text, model=s.gemini_model, provider=self.name)

    def is_available(self) -> bool:
        """True if an API key is set. No network call. Never raises."""
        try:
            return bool(self._settings.gemini_api_key)
        except Exception:
            return False


def _reply_text(reply) -> str:
    """The reply content is a string, or a list of blocks (newer versions)."""
    content = reply.content
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "".join(parts)


def _chain(exc: BaseException) -> list[BaseException]:
    """The error plus the errors that caused it (LangChain wraps Google's)."""
    found: list[BaseException] = []
    while exc is not None and exc not in found and len(found) < 6:
        found.append(exc)
        exc = exc.__cause__ or exc.__context__
    return found


def _status(errors: list[BaseException]) -> int | None:
    """The HTTP status code, if any error in the chain has one."""
    for e in errors:
        for attr in ("code", "status_code"):
            value = getattr(e, attr, None)
            if isinstance(value, int):
                return value
    return None


def _translate(exc: Exception, s: LLMSettings) -> LLMError:
    """Turn a library error into one of our simple errors.

    Messages are our own fixed text. We never copy the library's text,
    because it can contain the API key.
    """
    errors = _chain(exc)
    names = " ".join(type(e).__name__.lower() for e in errors)
    status = _status(errors)

    if any(isinstance(e, (httpx.TimeoutException, TimeoutError)) for e in errors):
        return Timeout()
    if status == 504 or "deadline" in names:
        return Timeout()
    if any(isinstance(e, (httpx.ConnectError, httpx.ProxyError, ConnectionError)) for e in errors):
        return Unreachable("Can't reach Gemini. Check your internet.")

    if "contextoverflow" in names:
        return ContextTooLong()
    if status in (401, 403) or "authentication" in names or "permissiondenied" in names:
        return SetupProblem(BAD_KEY_MESSAGE)
    if status == 404 or "modelnotfound" in names:
        return SetupProblem(f"Model not found. Check GEMINI_MODEL ({s.gemini_model}).")
    if status == 429 or "ratelimit" in names:
        return RateLimited()
    if status in (500, 502, 503):
        return Unreachable("Can't reach Gemini right now. Try again in a moment.")
    if status == 400:
        text = " ".join(str(e).lower() for e in errors)
        if "api key" in text:
            return SetupProblem(BAD_KEY_MESSAGE)
        if "token" in text and any(w in text for w in ("exceed", "limit", "too")):
            return ContextTooLong()
    return Unknown()
