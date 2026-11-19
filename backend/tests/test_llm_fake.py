import pytest

from app.errors import AppError
from app.llm import (
    ContextTooLong, LLMError, LLMRequest, RateLimited, SetupProblem,
    Timeout, Unknown, Unreachable, get_provider,
)
from app.llm.base import MAX_ATTEMPTS, RETRY_WAIT_SECONDS, BaseProvider
from app.llm.fake import FakeProvider

REQ = LLMRequest(system_prompt="rules", user_prompt="question")


def test_normal_call_returns_text_model_and_provider():
    r = FakeProvider(["SELECT 1"]).generate(REQ)
    assert (r.text, r.model, r.provider) == ("SELECT 1", "fake-model", "fake")


def test_fake_records_requests():
    p = FakeProvider(["a"])
    p.generate(REQ)
    assert p.requests == [REQ]


@pytest.mark.parametrize("error_class", [Unreachable, RateLimited])
def test_temporary_errors_retry_once_then_succeed(error_class):
    p = FakeProvider([error_class(), "SELECT 1"])
    assert p.generate(REQ).text == "SELECT 1"
    assert len(p.requests) == 2


@pytest.mark.parametrize("error_class", [Unreachable, RateLimited])
def test_temporary_errors_give_up_after_max_attempts(error_class):
    p = FakeProvider([error_class()])  # last reply repeats
    with pytest.raises(error_class):
        p.generate(REQ)
    assert len(p.requests) == MAX_ATTEMPTS == 2


@pytest.mark.parametrize("error_class", [SetupProblem, Timeout, ContextTooLong, Unknown])
def test_other_errors_are_never_retried(error_class):
    p = FakeProvider([error_class(), "SELECT 1"])
    with pytest.raises(error_class):
        p.generate(REQ)
    assert len(p.requests) == 1


def test_retry_waits_before_trying_again():
    waits = []
    p = FakeProvider([Unreachable(), "ok"])
    p._sleep = waits.append
    p.generate(REQ)
    assert waits == [RETRY_WAIT_SECONDS]


def test_errors_are_app_errors_with_llm_codes():
    for cls in (SetupProblem, Unreachable, RateLimited, Timeout, ContextTooLong, Unknown):
        e = cls()
        assert isinstance(e, LLMError) and isinstance(e, AppError)
        assert e.code.startswith("llm_") and e.message


def test_is_available():
    assert FakeProvider(available=True).is_available() is True
    assert FakeProvider(available=False).is_available() is False


def test_get_provider_unknown_name_is_setup_problem():
    with pytest.raises(SetupProblem):
        get_provider("nope")


def test_fake_is_a_base_provider():
    assert isinstance(FakeProvider(), BaseProvider)
