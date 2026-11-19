from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.errors import add_error_handlers
from app.api.routes import log_requests, router

app = FastAPI(title="Text-to-SQL")
add_error_handlers(app)
app.middleware("http")(log_requests)
app.include_router(router)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@app.get("/health")
def health():
    return {"status": "ok"}


# Serve the plain HTML/JS page. Mounted last so API routes win.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
