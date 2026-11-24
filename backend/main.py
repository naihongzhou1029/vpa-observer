"""FastAPI application entrypoint for VPA-View."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from backend import db, fetcher, scheduler, vpa

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Initialize SQLite schema and background market sync scheduler."""
    db.init_db()
    scheduler.start_scheduler()
    try:
        yield
    finally:
        scheduler.stop_scheduler()


app = FastAPI(title="VPA-View", version="0.1.0", lifespan=lifespan)


class WatchlistAddRequest(BaseModel):
    symbol: str
    name: str = ""


class WatchlistTagsRequest(BaseModel):
    tags: list[str] = []


class SyncRequest(BaseModel):
    symbol: str | None = None
    full: bool = False


@app.get("/api/watchlist")
def api_get_watchlist() -> dict[str, Any]:
    """Return all saved watchlist items with latest daily snapshot."""
    return {"items": db.get_watchlist()}


@app.post("/api/watchlist")
def api_add_watchlist(req: WatchlistAddRequest) -> dict[str, Any]:
    """Normalize symbol, persist to watchlist, and fetch initial OHLCV data."""
    try:
        symbol, market = fetcher.normalize_symbol(req.symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    item = db.upsert_watchlist_item(symbol, market, name=req.name.strip())
    sync_res = fetcher.fetch_and_store(symbol, full=True)
    updated_item = db.get_watchlist_item(symbol) or item
    return {"item": updated_item, "sync": sync_res}


@app.delete("/api/watchlist/{symbol}")
def api_delete_watchlist(symbol: str) -> dict[str, Any]:
    """Delete a symbol from the watchlist."""
    removed = db.delete_watchlist_item(symbol.upper())
    if not removed:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found in watchlist")
    return {"deleted": symbol.upper()}


@app.put("/api/watchlist/{symbol}/tags")
def api_update_watchlist_tags(symbol: str, req: WatchlistTagsRequest) -> dict[str, Any]:
    """Update color tags for a symbol in the watchlist."""
    updated = db.update_watchlist_tags(symbol.upper(), req.tags)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Symbol {symbol} not found in watchlist")
    return {"item": updated}


@app.get("/api/bars")
def api_get_bars(
    symbol: str = Query(..., description="Ticker symbol, e.g. 2330, 2330.TW, NVDA"),
    interval: str = Query("1d", description="Timeframe: 5m, 15m, 60m, 1d, 1w, 1mo"),
    limit: int = Query(1200, ge=20, le=3000),
) -> dict[str, Any]:
    """Return OHLCV bars, volume grades, and VPA markers for chart rendering."""
    if interval not in fetcher.SUPPORTED_INTERVALS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported interval '{interval}'. Allowed: {fetcher.SUPPORTED_INTERVALS}",
        )

    try:
        canonical, market = fetcher.normalize_symbol(symbol)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    bars = fetcher.get_bars_for_chart(canonical, interval, limit=limit)
    analysis = vpa.analyze_vpa(bars)
    watchlist_item = db.get_watchlist_item(canonical)

    display_name = watchlist_item["name"] if watchlist_item else ""
    needs_bilingual = (
        market == "TW"
        and not (fetcher.has_cjk(display_name) and fetcher.has_latin(display_name))
    )
    if not display_name or display_name == canonical or needs_bilingual:
        resolved = fetcher.resolve_display_name(canonical)
        if resolved and resolved != display_name:
            display_name = resolved
            if watchlist_item:
                db.update_watchlist_name(canonical, display_name)

    return {
        "symbol": canonical,
        "market": market,
        "name": display_name or canonical,
        "interval": interval,
        "bars": analysis["bars"],
        "vpa_markers": analysis["markers"],
        "summary": analysis["summary"],
    }


@app.post("/api/sync")
def api_sync(req: SyncRequest) -> dict[str, Any]:
    """Manually synchronize one symbol or the entire watchlist."""
    if req.symbol:
        canonical, market = fetcher.normalize_symbol(req.symbol)
        if not db.get_watchlist_item(canonical):
            db.upsert_watchlist_item(canonical, market)
        results = scheduler.sync_symbols(symbols=[canonical], full=req.full)
    else:
        results = scheduler.sync_symbols(full=req.full)
    return {"results": results}


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    """Return backend health, DB statistics, and scheduler status."""
    return {
        "status": "ok",
        "db": db.get_db_stats(),
        "scheduler": scheduler.get_scheduler_status(),
    }


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")

    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(str(FRONTEND_DIR / "index.html"))
