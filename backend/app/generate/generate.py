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

        print(f"\n[LLM DEBUG] Provider: {response.provider}")
        print(f"[LLM DEBUG] Model: {response.model}")
        print(f"[LLM DEBUG] Raw response:\n{response.text}")

        result = read_reply(response.text)

        print(f"\n[PARSE DEBUG] Status: {result.status}")
        print(f"[PARSE DEBUG] Result: {result}")

        return result
    
    except LLMError as e:
        print(f"\n[LLM ERROR] {e.message}")
        return GenerateQueryResult(status=ERROR, message=e.message)
