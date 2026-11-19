import re
from dataclasses import dataclass, field

PLAIN_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass
class Column:
    name: str
    type: str            # simplified, e.g. "VARCHAR" (may be "" if unknown)
    nullable: bool
    primary_key: bool


@dataclass
class ForeignKey:
    columns: list[str]
    ref_table: str
    ref_columns: list[str]


@dataclass
class Table:
    name: str
    kind: str            # "table" | "view"
    columns: list[Column]
    foreign_keys: list[ForeignKey]


@dataclass
class SchemaInfo:
    db_type: str
    tables: list[Table]
    warnings: list[str] = field(default_factory=list)

    def render_text(self) -> str:
        """The compact text that Generate Query puts in the prompt.

        Fixed format: the same schema always gives the same text.
        """
        return "\n\n".join(_render_table(t) for t in self.tables)


def quote_if_needed(name: str) -> str:
    """Plain names stay as they are. Anything else gets double quotes,
    so the model also writes it correctly in SQL."""
    if PLAIN_IDENTIFIER.match(name):
        return name
    return '"' + name.replace('"', '""') + '"'


def _names(names: list[str]) -> str:
    return ", ".join(quote_if_needed(n) for n in names)


def _render_table(table: Table) -> str:
    lines = [f"{table.kind.upper()} {quote_if_needed(table.name)}"]
    for col in table.columns:
        parts = [quote_if_needed(col.name)]
        if col.type:
            parts.append(col.type)
        if col.primary_key:
            parts.append("PRIMARY KEY")
        elif not col.nullable:
            parts.append("NOT NULL")
        lines.append("  " + " ".join(parts))
    for fk in table.foreign_keys:
        target = quote_if_needed(fk.ref_table)
        if fk.ref_columns:
            target += f"({_names(fk.ref_columns)})"
        lines.append(f"  FOREIGN KEY ({_names(fk.columns)}) -> {target}")
    return "\n".join(lines)
