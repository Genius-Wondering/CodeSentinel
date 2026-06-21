from fastapi import FastAPI
from app.api.routes import router
from app.db import init_db

app = FastAPI(
    title="CodeSentinel",
    description="AI-powered multi-agent code understanding system. Index any repo, then ask questions about it.",
    version="0.5.0",
)

app.include_router(router, prefix="/api/v1")    # Register routes to the FastAPI application


@app.on_event("startup")
def _on_startup():
    # Creates the SQLite (or MySQL/Postgres) tables on first run; no-op if
    # they already exist. See app/db.py / app/db_models.py.
    init_db()


@app.get("/")
def root():
    return {
        "service": "CodeSentinel",
        "version": "0.5.0",
        "docs": "/docs",
    }
