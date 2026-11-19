from app.generate.models import ERROR, GenerateQueryInput, GenerateQueryResult
from app.generate.prompt import build_request
from app.generate.reply import read_reply
from app.llm import LLMError, LLMProvider


def generate_query(inp: GenerateQueryInput, provider: LLMProvider) -> GenerateQueryResult:
    """Question + schema -> SQL, a clarifying question, or "not found".

    Never raises for model problems: they come back as status "error".
    """
    try:
        response = provider.generate(build_request(inp))
    except LLMError as e:
        return GenerateQueryResult(status=ERROR, message=e.message)
    return read_reply(response.text)
