"""SSA-001 FastAPI entry point. Other lanes add their API routes here."""

import psycopg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from backend.config import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Supervised Service Agent")
    app.state.settings = settings or Settings.from_env()

    @app.get("/healthz")
    def healthz():
        try:
            with psycopg.connect(
                app.state.settings.database_url, connect_timeout=3
            ) as conn:
                conn.execute("SELECT 1").fetchone()
        except psycopg.Error:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "unavailable"},
            )
        return {"status": "ok", "database": "reachable"}

    return app


app = create_app()
