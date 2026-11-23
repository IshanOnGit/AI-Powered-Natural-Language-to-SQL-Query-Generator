import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.api.errors import BadInput, NotConnected, QueryUnavailable
from app.connection import Connection, ConnectionInput, connect
from app.execution import execute
from app.llm import get_provider
from app.pipeline import READY, generate_validated_query
from app.schema import extract_schema
from app.validation import ValidatedQuery

log = logging.getLogger("app.api")
router = APIRouter()
PROVIDER_NAMES = ("gemini", "ollama")


# ---- session state: one user, in memory only ----

@dataclass
class SessionState:
    connection: Connection | None = None
    schema_text: str | None = None
    table_count: int = 0
    schema_warnings: list[str] = field(default_factory=list)
    pending: tuple[str, ValidatedQuery] | None = None   # at most ONE approved-to-show query

    def clear(self) -> None:
        if self.connection is not None:
            self.connection.close()       # also forgets the password
        self.connection = None
        self.schema_text = None
        self.table_count = 0
        self.schema_warnings = []
        self.pending = None


session = SessionState()


def _require_connection() -> Connection:
    if session.connection is None or session.connection.closed:
        raise NotConnected()
    return session.connection


def _read_schema(connection: Connection) -> dict:
    info = extract_schema(connection)
    session.schema_text = info.render_text()

    ## Uncomment these lines to see the schema in the server logs, for debugging.
    #print("\n========== DATABASE SCHEMA ==========")
    #print(session.schema_text)
    #print("======================================\n")

    session.table_count = len(info.tables)
    session.schema_warnings = list(info.warnings)
    return {"db_type": connection.db_type, "table_count": session.table_count,
            "warnings": session.schema_warnings}


# ---- request bodies (the browser never sends SQL) ----

class ConnectBody(BaseModel):
    db_type: str
    host: str | None = None
    port: int | None = None
    database: str | None = None
    username: str | None = None
    password: str | None = None


class GenerateBody(BaseModel):
    question: str
    provider: str | None = None


class ExecuteBody(BaseModel):
    query_id: str


# ---- endpoints ----

@router.post("/connect")
def connect_endpoint(body: ConnectBody):
    session.clear()
    connection = connect(ConnectionInput(**body.model_dump()))
    try:
        result = _read_schema(connection)
    except Exception:
        connection.close()
        raise
    session.connection = connection
    return result


@router.post("/refresh-schema")
def refresh_schema():
    connection = _require_connection()
    session.pending = None
    return _read_schema(connection)


@router.post("/disconnect")
def disconnect():
    session.clear()
    return {"connected": False}


@router.post("/generate")
def generate(body: GenerateBody):
    connection = _require_connection()
    question = body.question.strip()
    if not question:
        raise BadInput("Please type a question.")
    provider = get_provider(body.provider)
    session.pending = None              # a new question replaces the old query

    outcome = generate_validated_query(question, provider, session.schema_text, connection.db_type)

    if outcome.status == READY:
        query_id = secrets.token_urlsafe(16)
        session.pending = (query_id, outcome.query)
        return {"status": READY, "query_id": query_id,
                "sql": outcome.query.sql, "changes": outcome.query.changes}
    response = {"status": outcome.status, "message": outcome.message}
    if outcome.reason:
        response["reason"] = outcome.reason
    return response


@router.post("/execute")
def execute_endpoint(body: ExecuteBody):
    pending = session.pending
    if pending is None or not secrets.compare_digest(pending[0], body.query_id):
        raise QueryUnavailable()
    connection = _require_connection()
    session.pending = None              # it can only run once, even if it fails
    result = execute(connection, pending[1])
    return {"columns": result.columns, "rows": result.rows,
            "row_count": result.row_count, "elapsed_ms": result.elapsed_ms}


@router.get("/status")
def status():
    connected = session.connection is not None and not session.connection.closed
    return {
        "connected": connected,
        "db_type": session.connection.db_type if connected else None,
        "table_count": session.table_count if connected else 0,
        "providers_available": _available_providers(),
    }


def _available_providers() -> list[str]:
    available = []
    for name in PROVIDER_NAMES:
        try:
            if get_provider(name).is_available():
                available.append(name)
        except Exception:
            pass                        # not set up -> not offered
    return available


# ---- logging: endpoint, status, timing only. Never bodies, questions or SQL. ----

async def log_requests(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    log.info("%s %s -> %s in %d ms", request.method, request.url.path,
             response.status_code, (time.perf_counter() - started) * 1000)
    return response
