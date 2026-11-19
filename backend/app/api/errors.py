import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.errors import AppError

log = logging.getLogger("app.api")


class NotConnected(AppError):
    def __init__(self):
        super().__init__("not_connected", "Connect to a database first.")


class QueryUnavailable(AppError):
    def __init__(self):
        super().__init__("query_unavailable",
                         "This query is no longer available. Please generate it again.")


class BadInput(AppError):
    def __init__(self, message: str):
        super().__init__("bad_input", message)


def status_for(error: AppError) -> int:
    """Pick the HTTP status from the error type."""
    if isinstance(error, NotConnected):
        return 409
    if isinstance(error, QueryUnavailable):
        return 404
    code = error.code
    if code.endswith("timeout"):
        return 504
    if code.endswith(("unreachable", "connection_lost", "rate_limited")):
        return 502
    return 400


def error_body(code: str, message: str) -> dict:
    return {"code": code, "message": message}


def add_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def on_app_error(request: Request, error: AppError):
        log.info("%s %s error=%s", request.method, request.url.path, error.code)
        return JSONResponse(error_body(error.code, error.message), status_code=status_for(error))

    @app.exception_handler(RequestValidationError)
    async def on_bad_request(request: Request, error: RequestValidationError):
        # The default handler echoes the submitted input, which can include
        # the password. We return only the field name and a fixed message.
        field = "request body"
        for e in error.errors():
            loc = [p for p in e.get("loc", ()) if p != "body"]
            if loc:
                field = str(loc[-1])
                break
        log.info("%s %s error=bad_input", request.method, request.url.path)
        return JSONResponse(
            error_body("bad_input", f"Missing or invalid field: {field}"), status_code=400)

    @app.exception_handler(StarletteHTTPException)
    async def on_http_error(request: Request, error: StarletteHTTPException):
        return JSONResponse(
            error_body("http_error", "That page or action does not exist."),
            status_code=error.status_code)

    @app.exception_handler(Exception)
    async def on_unexpected(request: Request, error: Exception):
        # Log only the kind of error, never its text (it could hold secrets).
        log.error("%s %s error=internal (%s)", request.method, request.url.path,
                  type(error).__name__)
        return JSONResponse(error_body("internal", "Something went wrong."), status_code=500)
