"""Gemini provider tests. A stand-in client is used: no key, no network."""
import httpx
import pytest
from google.genai import errors as google_errors
from langchain_core.messages import AIMessage

from app.llm import (
    ContextTooLong, LLMRequest, RateLimited, SetupProblem, Timeout,
    Unknown, Unreachable, get_provider,
)
from app.llm.base import MAX_ATTEMPTS
from app.llm.gemini_provider import GeminiProvider
from app.llm.settings import LLMSettings

REQ = LLMRequest(system_prompt="rules", user_prompt="question")
SECRET = "AIzaSy-THIS-IS-A-SECRET-KEY"


def make_settings(key=SECRET) -> LLMSettings:
    return LLMSettings(
        provider="gemini", temperature=0, timeout_seconds=30,
        gemini_api_key=key, gemini_model="gemini-test",
        ollama_base_url="http://localhost:11434", ollama_model="x", ollama_num_ctx=8192,
    )


class StandInClient:
    """Plays the part of ChatGoogleGenerativeAI. Replies are used in order,
    the last one repeats. An Exception item is raised."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def invoke(self, messages):
        item = self.replies[min(self.calls, len(self.replies) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item if isinstance(item, AIMessage) else AIMessage(content=item)


def provider_with(replies, key=SECRET):
    p = GeminiProvider(make_settings(key), sleep=lambda seconds: None)
    client = StandInClient(replies)
    p._make_client = lambda: client
    return p, client


def api_error(cls, code, message="boom"):
    return cls(code, {"error": {"code": code, "message": message, "status": "X"}})


# --- the shared conformance tests (provider spec, section 7) ---

def test_normal_call_returns_text_model_and_provider():
    p, _ = provider_with(["SELECT 1"])
    r = p.generate(REQ)
    assert (r.text, r.model, r.provider) == ("SELECT 1", "gemini-test", "gemini")


def test_missing_key_is_setup_problem_and_makes_no_call():
    p, client = provider_with(["SELECT 1"], key=None)
    with pytest.raises(SetupProblem) as e:
        p.generate(REQ)
    assert "API key is not set" in e.value.message
    assert client.calls == 0


def test_unreachable_raises_unreachable_after_one_retry():
    p, client = provider_with([httpx.ConnectError("no route")])
    with pytest.raises(Unreachable):
        p.generate(REQ)
    assert client.calls == MAX_ATTEMPTS


def test_slow_response_raises_timeout_without_retry():
    p, client = provider_with([httpx.ReadTimeout("slow")])
    with pytest.raises(Timeout):
        p.generate(REQ)
    assert client.calls == 1


@pytest.mark.parametrize("temporary", [httpx.ConnectError("x"), api_error(google_errors.ClientError, 429)])
def test_temporary_errors_retry_once_then_succeed(temporary):
    p, client = provider_with([temporary, "SELECT 1"])
    assert p.generate(REQ).text == "SELECT 1"
    assert client.calls == 2


@pytest.mark.parametrize("permanent", [
    api_error(google_errors.ClientError, 403),
    api_error(google_errors.ClientError, 404),
    httpx.ReadTimeout("slow"),
    ValueError("anything else"),
])
def test_other_errors_are_never_retried(permanent):
    p, client = provider_with([permanent, "SELECT 1"])
    with pytest.raises(Exception):
        p.generate(REQ)
    assert client.calls == 1


def test_api_key_never_appears_in_error_messages():
    leaky = [
        ValueError(f"request failed, key={SECRET}"),
        api_error(google_errors.ClientError, 400, f"API key not valid: {SECRET}"),
        api_error(google_errors.ClientError, 403, SECRET),
        api_error(google_errors.ServerError, 500, SECRET),
        api_error(google_errors.ClientError, 404, SECRET),
        api_error(google_errors.ClientError, 429, SECRET),
        httpx.ConnectError(SECRET),
        httpx.ReadTimeout(SECRET),
    ]
    for error in leaky:
        p, _ = provider_with([error])
        with pytest.raises(Exception) as e:
            p.generate(REQ)
        assert SECRET not in str(e.value)
        assert SECRET not in e.value.message
        assert e.value.__cause__ is None


def test_settings_repr_hides_the_key():
    assert SECRET not in repr(make_settings())


# --- error translation ---

@pytest.mark.parametrize("error, expected", [
    (api_error(google_errors.ClientError, 429), RateLimited),
    (api_error(google_errors.ClientError, 401), SetupProblem),
    (api_error(google_errors.ClientError, 403), SetupProblem),
    (api_error(google_errors.ClientError, 404), SetupProblem),
    (api_error(google_errors.ClientError, 400, "API key not valid"), SetupProblem),
    (api_error(google_errors.ClientError, 400, "input token count exceeds the limit"), ContextTooLong),
    (api_error(google_errors.ClientError, 400, "something else"), Unknown),
    (api_error(google_errors.ServerError, 503), Unreachable),
    (api_error(google_errors.ServerError, 504), Timeout),
    (RuntimeError("???"), Unknown),
])
def test_library_errors_become_simple_errors(error, expected):
    p, _ = provider_with([error])
    with pytest.raises(expected):
        p.generate(REQ)


def test_wrapped_error_is_found_through_its_cause():
    wrapper = RuntimeError("wrapped by langchain")
    wrapper.__cause__ = api_error(google_errors.ClientError, 429)
    p, _ = provider_with([wrapper, "ok"])
    assert p.generate(REQ).text == "ok"  # rate limit -> retried


# --- replies ---

def test_empty_reply_is_unknown_not_a_blank_answer():
    p, _ = provider_with([""])
    with pytest.raises(Unknown):
        p.generate(REQ)


def test_block_style_reply_is_joined():
    p, _ = provider_with([AIMessage(content=[{"type": "text", "text": "SELECT "}, {"type": "text", "text": "1"}])])
    assert p.generate(REQ).text == "SELECT 1"


# --- availability and get_provider ---

def test_is_available_means_key_is_set():
    assert GeminiProvider(make_settings()).is_available() is True
    assert GeminiProvider(make_settings(key=None)).is_available() is False


def test_get_provider_gemini(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    assert isinstance(get_provider("gemini"), GeminiProvider)
    assert isinstance(get_provider("  Gemini "), GeminiProvider)


def test_real_client_is_built_with_our_settings():
    """Builds the real LangChain client (no call is made)."""
    client = GeminiProvider(make_settings())._make_client()
    assert client.model.endswith("gemini-test")
    assert client.max_retries == 0
    assert client.timeout == 30
