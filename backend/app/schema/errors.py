from app.errors import AppError


class SchemaError(AppError):
    """Base for all schema-reading errors. Codes start with "schema_".

    Messages are our own fixed text, never copied from the database driver
    (driver errors can echo the connection string and password).
    """

    code = "schema_unknown"
    default_message = "Something went wrong reading the schema."

    def __init__(self, message: str | None = None):
        super().__init__(self.code, message or self.default_message)


class EmptySchema(SchemaError):
    code = "schema_empty"
    default_message = "No tables found. Check the database or schema name."


class PermissionDenied(SchemaError):
    code = "schema_permission_denied"
    default_message = "This user can't read the database structure."


class ConnectionLost(SchemaError):
    code = "schema_connection_lost"
    default_message = "Lost connection while reading the schema."


class Timeout(SchemaError):
    code = "schema_timeout"
    default_message = "Reading the schema took too long."


class Unknown(SchemaError):
    code = "schema_unknown"
    default_message = "Something went wrong reading the schema."
