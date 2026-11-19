from dataclasses import dataclass

SQL = "sql"
CLARIFICATION = "clarification"
NOT_FOUND = "not_found"
ERROR = "error"


@dataclass
class GenerateQueryInput:
    question: str       # the user's plain-English question
    schema_text: str    # from SchemaInfo.render_text()
    db_type: str        # "postgresql" | "mysql" | "sqlite"


@dataclass
class GenerateQueryResult:
    status: str                  # "sql" | "clarification" | "not_found" | "error"
    sql: str | None = None       # set when status == "sql"
    message: str | None = None   # set for the other three
