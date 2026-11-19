from app.errors import AppError


class DBConnectionError(AppError):
    """Base for all connection errors. Codes start with "db_".

    (Named DBConnectionError so it doesn't clash with Python's ConnectionError.)
    Messages are our own fixed text, never copied from the database driver:
    driver errors often contain the connection string and password.
    """

    code = "db_unknown"
    default_message = "Something went wrong connecting."

    def __init__(self, message: str | None = None):
        super().__init__(self.code, message or self.default_message)


class InvalidInput(DBConnectionError):
    code = "db_invalid_input"
    default_message = "The connection details are not valid."


class AuthFailed(DBConnectionError):
    code = "db_auth_failed"
    default_message = "Login failed. Check your username and password."


class Unreachable(DBConnectionError):
    code = "db_unreachable"
    default_message = "Can't reach the database at that host and port."


class DatabaseNotFound(DBConnectionError):
    code = "db_not_found"
    default_message = "The database was not found."


class UnsupportedDatabase(DBConnectionError):
    code = "db_unsupported"
    default_message = "Only PostgreSQL, MySQL and SQLite are supported."


class Unknown(DBConnectionError):
    code = "db_unknown"
    default_message = "Something went wrong connecting."
