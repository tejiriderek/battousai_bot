"""Read-only HTTP surface so hosts can health-check the process."""

from __future__ import annotations

import time
from typing import Any, Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

_started = time.time()


def create_app(
    snapshot_provider: Callable[[], dict[str, Any]],
    running_provider: Callable[[], bool],
) -> FastAPI:
    app = FastAPI(title="Battoujutsu Forex Scanner", docs_url=None, redoc_url=None)

    @app.get("/status")
    def status() -> JSONResponse:
        try:
            snapshot = snapshot_provider()
        except Exception as exc:
            snapshot = {"error": str(exc)}
        body = {
            "scanner_running": bool(running_provider()),
            "uptime_s": int(time.time() - _started),
            "snapshot": snapshot,
        }
        return JSONResponse(body)

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "scanner_running": bool(running_provider())})

    return app
