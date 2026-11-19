import re

from app.generate.models import (
    CLARIFICATION, ERROR, NOT_FOUND, SQL, GenerateQueryResult,
)

BAD_REPLY = "Couldn't understand the model's reply."

THINK_BLOCK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
LOOKS_LIKE_SQL = re.compile(r"(select|with)\b", re.IGNORECASE)


def _clean(text: str) -> str:
    text = THINK_BLOCK.sub("", text).strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""   # drop ```sql line
        text = text.strip()
        if text.endswith("```"):
            text = text[:-3]
    return text.strip().strip("`").strip()


def read_reply(text: str) -> GenerateQueryResult:
    text = _clean(text or "")
    first_line = text.split("\n", 1)[0].strip()

    for marker, status in (("CLARIFY:", CLARIFICATION), ("NOT_FOUND:", NOT_FOUND)):
        if first_line.upper().startswith(marker):
            message = first_line[len(marker):].strip()
            if message:
                return GenerateQueryResult(status=status, message=message)
            return GenerateQueryResult(status=ERROR, message=BAD_REPLY)

    if LOOKS_LIKE_SQL.match(text):
        return GenerateQueryResult(status=SQL, sql=text)

    return GenerateQueryResult(status=ERROR, message=BAD_REPLY)
