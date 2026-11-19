from dataclasses import dataclass, field

# Rejection reason codes (spec section 5)
EMPTY = "EMPTY"
SYNTAX_ERROR = "SYNTAX_ERROR"
MULTIPLE_STATEMENTS = "MULTIPLE_STATEMENTS"
NOT_READ_ONLY = "NOT_READ_ONLY"
FORBIDDEN_CLAUSE = "FORBIDDEN_CLAUSE"
FORBIDDEN_FUNCTION = "FORBIDDEN_FUNCTION"

# Only validate() holds this. A ValidatedQuery built without it is refused,
# so unvalidated SQL can't be passed to Query Execution by mistake.
_SEAL = object()


@dataclass(frozen=True)
class ValidatedQuery:
    sql: str                                  # exactly what the user sees and what will run
    changes: list[str] = field(default_factory=list)   # e.g. ["Added LIMIT 100"]
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        if self._seal is not _SEAL:
            raise TypeError("A ValidatedQuery can only be created by validate().")


@dataclass
class ValidationResult:
    ok: bool
    query: ValidatedQuery | None = None
    reason: str | None = None      # one of the codes above
    message: str | None = None     # friendly text for the user
