class SecretStr:
    """Holds a password. Printing it, logging it or putting it in an error
    always shows "***". Only get_secret_value() returns the real text."""

    __slots__ = ("_value",)

    def __init__(self, value: str):
        object.__setattr__(self, "_value", str(value))

    def get_secret_value(self) -> str:
        return self._value

    def __setattr__(self, name, value):
        raise AttributeError("SecretStr is read-only")

    def __repr__(self) -> str:
        return "SecretStr('***')"

    def __str__(self) -> str:
        return "***"

    def __format__(self, spec: str) -> str:
        return format("***", spec)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __eq__(self, other) -> bool:
        return isinstance(other, SecretStr) and self._value == other._value

    def __hash__(self) -> int:
        return hash(self._value)

    def __reduce__(self):
        raise TypeError("SecretStr can't be pickled")  # keeps it out of dumps
