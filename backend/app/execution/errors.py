from app.errors import AppError


class ExecutionError(AppError):
    """Base for all query-running errors. Codes start with "query_".

    Messages are our own fixed text, except QueryFailed, which adds the
    database's short error text (with the password scrubbed out).
    """

    code = "query_unknown"
    default_message = "Something went wrong running the query."

    def __init__(self, message: str | None = None):
        super().__init__(self.code, message or self.default_message)


class Timeout(ExecutionError):
    code = "query_timeout"
    default_message = "The query took too long and was stopped."


class QueryFailed(ExecutionError):
    code = "query_failed"
    default_message = "The database couldn't run this query."


class ConnectionLost(ExecutionError):
    code = "query_connection_lost"
    default_message = "Lost the connection to the database."


class Unknown(ExecutionError):
    code = "query_unknown"
    default_message = "Something went wrong running the query."
