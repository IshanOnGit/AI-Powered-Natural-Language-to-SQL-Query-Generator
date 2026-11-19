from dataclasses import dataclass


@dataclass
class ExecutionResult:
    columns: list[str]
    rows: list[list]      # each value: str, int, float, bool or None
    row_count: int        # len(rows)
    elapsed_ms: int
