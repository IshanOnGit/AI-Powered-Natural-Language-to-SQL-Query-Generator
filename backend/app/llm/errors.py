from app.errors import AppError


class LLMError(AppError):
    """Base for all model-provider errors. Codes start with "llm_"."""

    code = "llm_unknown"
    default_message = "Something went wrong with the model."

    def __init__(self, message: str | None = None):
        super().__init__(self.code, message or self.default_message)


class SetupProblem(LLMError):
    code = "llm_setup_problem"
    default_message = "The model is not set up correctly. Check your settings."


class Unreachable(LLMError):
    code = "llm_unreachable"
    default_message = "Can't reach the model."


class RateLimited(LLMError):
    code = "llm_rate_limited"
    default_message = "Too many requests. Wait a moment and try again."


class Timeout(LLMError):
    code = "llm_timeout"
    default_message = "The model took too long to answer."


class ContextTooLong(LLMError):
    code = "llm_context_too_long"
    default_message = "The schema is too large for this model."


class Unknown(LLMError):
    code = "llm_unknown"
    default_message = "Something went wrong with the model."
