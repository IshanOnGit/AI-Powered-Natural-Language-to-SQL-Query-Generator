from dataclasses import dataclass

from app import generate as g
from app.llm import LLMProvider
from app.validation import ValidatedQuery, validate

READY = "ready"
CLARIFICATION = "clarification"
NOT_FOUND = "not_found"
REJECTED = "rejected"
ERROR = "error"


@dataclass
class PipelineOutcome:
    status: str                          # "ready" | "clarification" | "not_found" | "rejected" | "error"
    query: ValidatedQuery | None = None  # set when status == "ready"
    message: str | None = None           # set for every other status
    reason: str | None = None            # validation reason code, when "rejected"


def generate_validated_query(
    question: str, provider: LLMProvider, schema_text: str, db_type: str,
) -> PipelineOutcome:
    """Question -> checked SQL (or a clear reason why not). Takes plain values,
    not the web session, so the benchmark can call it too."""
    result = g.generate_query(g.GenerateQueryInput(question, schema_text, db_type), provider)

    if result.status != g.SQL:
        # clarification / not_found / error use the same words in both places
        return PipelineOutcome(status=result.status, message=result.message)

    check = validate(result.sql, db_type)
    if check.ok:
        return PipelineOutcome(status=READY, query=check.query)
    return PipelineOutcome(status=REJECTED, message=check.message, reason=check.reason)
