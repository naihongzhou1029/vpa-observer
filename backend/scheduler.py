"""APScheduler jobs for automated TW and US market synchronization."""

from __future__ import annotations

import logging
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from backend import db, fetcher

logger = logging.getLogger("vpa_view.scheduler")

_scheduler: BackgroundScheduler | None = None


def sync_symbols(
    symbols: list[str] | None = None,
    intervals: tuple[str, ...] | list[str] | None = None,
    market_filter: str | None = None,
    full: bool = False,
) -> list[dict[str, Any]]:
    """Synchronize OHLCV bars for all or selected watchlist symbols."""
    if symbols:
        targets = symbols
    else:
        wl = db.get_watchlist()
        targets = [
            item["symbol"]
            for item in wl
            if market_filter is None or item["market"] == market_filter
        ]

    results: list[dict[str, Any]] = []
    for sym in targets:
        try:
            res = fetcher.fetch_and_store(sym, intervals=intervals, full=full)
            results.append(res)
        except Exception as exc:
            logger.exception("Sync failed for %s: %s", sym, exc)
            results.append({"symbol": sym, "written": {}, "errors": {"sync": str(exc)}})
    return results


def _job_tw_intraday() -> None:
    logger.info("Running scheduled TW intraday sync")
    sync_symbols(intervals=["5m", "15m", "60m"], market_filter="TW")


def _job_tw_daily() -> None:
    logger.info("Running scheduled TW daily sync")
    sync_symbols(intervals=["1d", "60m", "15m", "5m"], market_filter="TW")


def _job_us_intraday() -> None:
    logger.info("Running scheduled US intraday sync")
    sync_symbols(intervals=["5m", "15m", "60m"], market_filter="US")


def _job_us_daily() -> None:
    logger.info("Running scheduled US daily sync")
    sync_symbols(intervals=["1d", "60m", "15m", "5m"], market_filter="US")


def start_scheduler() -> BackgroundScheduler:
    """Start APScheduler background jobs if not already running."""
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler

    scheduler = BackgroundScheduler()
    # Taiwan Stock Exchange: Mon-Fri 09:00-13:45 Asia/Taipei every 15m
    scheduler.add_job(
        _job_tw_intraday,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-13",
            minute="*/15",
            timezone="Asia/Taipei",
        ),
        id="tw_intraday",
        replace_existing=True,
    )
    # Taiwan close daily settle: 14:30 & 18:00 Asia/Taipei
    scheduler.add_job(
        _job_tw_daily,
        CronTrigger(
            day_of_week="mon-fri",
            hour="14,18",
            minute="30,0",
            timezone="Asia/Taipei",
        ),
        id="tw_daily",
        replace_existing=True,
    )
    # US Market intraday: Mon-Fri 09:30-16:00 America/New_York every 30m
    scheduler.add_job(
        _job_us_intraday,
        CronTrigger(
            day_of_week="mon-fri",
            hour="9-16",
            minute="0,30",
            timezone="America/New_York",
        ),
        id="us_intraday",
        replace_existing=True,
    )
    # US Market close daily settle: 16:30 & 20:00 America/New_York
    scheduler.add_job(
        _job_us_daily,
        CronTrigger(
            day_of_week="mon-fri",
            hour="16,20",
            minute="30,0",
            timezone="America/New_York",
        ),
        id="us_daily",
        replace_existing=True,
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def stop_scheduler() -> None:
    """Shut down APScheduler cleanly."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def get_scheduler_status() -> dict[str, Any]:
    """Return current scheduler state and upcoming job run times."""
    if _scheduler is None or not _scheduler.running:
        return {"running": False, "jobs": []}
    jobs = [
        {
            "id": job.id,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in _scheduler.get_jobs()
    ]
    return {"running": True, "jobs": jobs}
