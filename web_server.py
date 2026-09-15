"""HTTP surface for health checks and TradingView webhook ingestion."""

# TradingView Pine Script template:
# // @version=6
# // indicator("Battousai TV Sender", overlay=true)
# // isBreakout = ta.crossover(close, ta.highest(close, 20)[1])
# // if isBreakout
# //     alert('{"pair":"'+ syminfo.ticker + '", "event":"breakout", "tv_price":'+ str.tostring(close) +', "tf":"H4", "time":"'+ str.tostring(time) +'"}', alert.freq_once_per_bar_close)

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from fastapi import Body, FastAPI
from fastapi.responses import JSONResponse

_started = time.time()
log = logging.getLogger(__name__)


def create_app(
    snapshot_provider: Callable[[], dict[str, Any]],
    running_provider: Callable[[], bool],
    tradingview_handler: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    tradingview_email_status_provider: Callable[[], dict[str, Any]] | None = None,
) -> FastAPI:
    app = FastAPI(title="Battoujutsu Forex Scanner", docs_url=None, redoc_url=None)

    @app.api_route("/status", methods=["GET", "HEAD"])
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
        if tradingview_email_status_provider:
            body["tradingview_email"] = tradingview_email_status_provider()
        return JSONResponse(body)

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "scanner_running": bool(running_provider())})

    @app.post("/tradingview-webhook")
    def tradingview_webhook(payload: dict[str, Any] = Body(default={})) -> JSONResponse:
        if tradingview_handler is None:
            return JSONResponse({"error": "TradingView webhook is not configured"}, status_code=503)
        try:
            result = tradingview_handler(payload)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:
            log.exception("TradingView webhook failed")
            return JSONResponse({"error": str(exc)}, status_code=500)
        return JSONResponse(result)

    return app
