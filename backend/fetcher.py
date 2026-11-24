"""Market data fetcher and timeframe aggregator for TW and US equities."""

from __future__ import annotations

import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import yfinance as yf

from backend import db

logger = logging.getLogger("vpa_view.fetcher")

RAW_INTERVALS = ("1d", "60m", "15m", "5m")
SUPPORTED_INTERVALS = ("5m", "15m", "60m", "1d", "1w", "1mo")

PERIOD_CONFIG = {
    "1d": {"full": "5y", "inc": "1mo"},
    "60m": {"full": "730d", "inc": "30d"},
    "15m": {"full": "60d", "inc": "14d"},
    "5m": {"full": "60d", "inc": "7d"},
}

TW_CODE_RE = re.compile(r"^\d{4,6}[A-Z]?$")
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
LATIN_RE = re.compile(r"[A-Za-z]")

_TW_NAME_CACHE: dict[str, str] = {}
_TW_NAME_CACHE_TS: float = 0.0
_TW_NAME_CACHE_TTL = 12 * 3600


def has_cjk(text: str) -> bool:
    """Return True if text contains at least one CJK character."""
    return bool(CJK_RE.search(text or ""))


def has_latin(text: str) -> bool:
    """Return True if text contains at least one ASCII Latin letter."""
    return bool(LATIN_RE.search(text or ""))


def infer_market(symbol: str) -> str:
    """Infer market code ('TW' or 'US') from a normalized symbol."""
    upper = symbol.upper()
    if upper.endswith(".TW") or upper.endswith(".TWO") or upper == "^TWII":
        return "TW"
    return "US"


def _probe_yfinance_has_data(symbol: str) -> bool:
    """Check quickly whether a symbol returns recent daily bars on Yahoo Finance."""
    try:
        df = yf.Ticker(symbol).history(period="5d", interval="1d", auto_adjust=False)
        return df is not None and not df.empty
    except Exception:
        return False


def normalize_symbol(raw_symbol: str) -> tuple[str, str]:
    """Normalize user input symbol into (canonical_symbol, market).

    Pure Taiwan stock codes like '2330' or '6488' probe '.TW' then '.TWO'.
    """
    cleaned = raw_symbol.strip().upper()
    if not cleaned:
        raise ValueError("Symbol cannot be empty")

    if cleaned in ("TWII", "^TWII"):
        return "^TWII", "TW"

    if cleaned.endswith(".TW") or cleaned.endswith(".TWO"):
        return cleaned, "TW"

    if TW_CODE_RE.match(cleaned):
        tw_candidate = f"{cleaned}.TW"
        if _probe_yfinance_has_data(tw_candidate):
            return tw_candidate, "TW"
        two_candidate = f"{cleaned}.TWO"
        if _probe_yfinance_has_data(two_candidate):
            return two_candidate, "TW"
        return tw_candidate, "TW"

    return cleaned, "US"


def _df_to_bars(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert a yfinance OHLCV DataFrame into clean bar dicts."""
    if df is None or df.empty:
        return []

    work = df.copy()
    if isinstance(work.columns, pd.MultiIndex):
        work.columns = work.columns.get_level_values(0)

    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    if not required_cols.issubset(set(work.columns)):
        return []

    work = work.dropna(subset=["Open", "High", "Low", "Close"])
    if work.empty:
        return []

    idx = pd.to_datetime(work.index)
    if idx.tzinfo is None:
        idx = idx.tz_localize(timezone.utc)
    else:
        idx = idx.tz_convert(timezone.utc)

    timestamps = [int(t.timestamp()) for t in idx]
    bars: list[dict[str, Any]] = []
    for ts, (_, row) in zip(timestamps, work.iterrows()):
        o = float(row["Open"])
        h = float(row["High"])
        l = float(row["Low"])
        c = float(row["Close"])
        v = float(row["Volume"]) if pd.notna(row["Volume"]) else 0.0
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        bars.append(
            {
                "ts": int(ts),
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": max(0.0, v),
            }
        )
    return bars


def _fetch_finmind_daily(symbol: str, days: int = 1825) -> list[dict[str, Any]]:
    """Optional FinMind fallback for Taiwan daily OHLCV when FINMIND_TOKEN is configured."""
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token or not (symbol.endswith(".TW") or symbol.endswith(".TWO")):
        return []

    stock_id = symbol.split(".")[0]
    start_date = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    params = {
        "dataset": "TaiwanStockPrice",
        "data_id": stock_id,
        "start_date": start_date,
        "token": token,
    }
    try:
        resp = httpx.get(
            "https://api.finmindtrade.com/api/v4/data",
            params=params,
            timeout=20.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != 200:
            return []
        bars: list[dict[str, Any]] = []
        for row in payload.get("data", []):
            dt = datetime.strptime(row["date"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
            o = float(row["open"])
            h = float(row["max"])
            l = float(row["min"])
            c = float(row["close"])
            v = float(row["Trading_Volume"])
            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                continue
            bars.append(
                {
                    "ts": int(dt.timestamp()),
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": max(0.0, v),
                }
            )
        return bars
    except Exception as exc:
        logger.warning("FinMind fallback failed for %s: %s", symbol, exc)
        return []


def _patch_twii_daily_volume(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fill missing zero-volume ^TWII daily bars using TWSE after-trading FMTQIK data.

    TWSE transaction counts (row[3]) match Yahoo Finance's historical ^TWII volume scale,
    keeping rolling 20-period volume averages consistent across bars.
    """
    if not bars or not any(b["volume"] <= 0 for b in bars):
        return bars

    try:
        resp = httpx.get(
            "https://www.twse.com.tw/rwd/zh/afterTrading/FMTQIK",
            params={"response": "json"},
            timeout=10.0,
        )
        resp.raise_for_status()
        payload = resp.json()
        rows = payload.get("data") or []
    except Exception as exc:
        logger.warning("TWSE FMTQIK volume fallback failed: %s", exc)
        return bars

    twse_counts: dict[str, float] = {}
    for row in rows:
        if len(row) < 4:
            continue
        parts = str(row[0]).strip().split("/")
        if len(parts) != 3:
            continue
        try:
            ad_year = int(parts[0]) + 1911
            date_key = f"{ad_year:04d}-{int(parts[1]):02d}-{int(parts[2]):02d}"
            tx_count = float(str(row[3]).replace(",", "").strip())
            if tx_count > 0:
                twse_counts[date_key] = tx_count
        except ValueError:
            continue

    if not twse_counts:
        return bars

    tz_tw = ZoneInfo("Asia/Taipei")
    for bar in bars:
        if bar["volume"] <= 0:
            date_key = datetime.fromtimestamp(bar["ts"], tz=tz_tw).strftime("%Y-%m-%d")
            if date_key in twse_counts:
                bar["volume"] = twse_counts[date_key]
    return bars


def _load_tw_official_names() -> dict[str, str]:
    """Fetch and cache official Taiwan stock/ETF Chinese names from TWSE and TPEx OpenAPI."""
    global _TW_NAME_CACHE, _TW_NAME_CACHE_TS
    now = time.time()
    if _TW_NAME_CACHE and (now - _TW_NAME_CACHE_TS) < _TW_NAME_CACHE_TTL:
        return _TW_NAME_CACHE

    mapping: dict[str, str] = {"^TWII": "加權指數"}
    sources = (
        ("https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL", "Code", "Name"),
        ("https://www.tpex.org.tw/openapi/v1/tpex_mainboard_quotes", "SecuritiesCompanyCode", "CompanyName"),
    )
    for url, code_key, name_key in sources:
        try:
            resp = httpx.get(url, timeout=10.0)
            resp.raise_for_status()
            rows = resp.json()
            if isinstance(rows, list):
                for row in rows:
                    code = str(row.get(code_key, "")).strip().upper()
                    zh = str(row.get(name_key, "")).strip()
                    if code and zh:
                        mapping[code] = zh
        except Exception as exc:
            logger.warning("Failed to fetch TW official names from %s: %s", url, exc)

    if len(mapping) > 1:
        _TW_NAME_CACHE = mapping
        _TW_NAME_CACHE_TS = now
    elif not _TW_NAME_CACHE:
        _TW_NAME_CACHE = mapping
    return _TW_NAME_CACHE


def _get_tw_official_zh_name(symbol: str) -> str:
    """Lookup official Chinese short name for a TW symbol (e.g. '2330.TW', '3374.TWO', '^TWII')."""
    upper = symbol.upper()
    mapping = _load_tw_official_names()
    if upper in mapping:
        return mapping[upper]
    code = upper.split(".")[0]
    return mapping.get(code, "")


def resolve_display_name(symbol: str, ticker: yf.Ticker | None = None) -> str:
    """Resolve human-readable display name; TW stocks combine official Chinese + English names."""
    t = ticker or yf.Ticker(symbol)
    en_name = ""
    try:
        info = t.info or {}
        raw = info.get("shortName") or info.get("longName") or ""
        en_name = " ".join(str(raw).split())
    except Exception:
        en_name = ""

    if infer_market(symbol) == "TW":
        zh_name = _get_tw_official_zh_name(symbol)
        if zh_name and en_name:
            if zh_name in en_name:
                return en_name[:80]
            if en_name in zh_name:
                return zh_name[:80]
            return f"{zh_name} {en_name}"[:80]
        if zh_name:
            return zh_name[:80]

    return (en_name or symbol)[:80]


def fetch_and_store(
    symbol: str,
    intervals: tuple[str, ...] | list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    """Download OHLCV data for given intervals and persist into SQLite."""
    target_intervals = list(intervals) if intervals else list(RAW_INTERVALS)
    ticker = yf.Ticker(symbol)
    written: dict[str, int] = {}
    errors: dict[str, str] = {}

    for interval in target_intervals:
        if interval not in PERIOD_CONFIG:
            continue
        has_existing = db.has_bars(symbol, interval)
        mode = "full" if (full or not has_existing) else "inc"
        period = PERIOD_CONFIG[interval][mode]
        bars: list[dict[str, Any]] = []

        try:
            df = ticker.history(period=period, interval=interval, auto_adjust=False)
            bars = _df_to_bars(df)
        except Exception as exc:
            errors[interval] = str(exc)
            logger.warning("yfinance fetch error (%s %s): %s", symbol, interval, exc)

        if not bars and interval == "1d" and infer_market(symbol) == "TW":
            bars = _fetch_finmind_daily(symbol)

        if bars and symbol == "^TWII" and interval == "1d":
            bars = _patch_twii_daily_volume(bars)

        if bars:
            written[interval] = db.upsert_bars(symbol, interval, bars)
        else:
            written[interval] = 0

    existing_item = db.get_watchlist_item(symbol)
    if existing_item:
        cur_name = existing_item.get("name") or ""
        needs_name = not cur_name or cur_name == symbol
        if infer_market(symbol) == "TW" and not (has_cjk(cur_name) and has_latin(cur_name)):
            needs_name = True
        name = resolve_display_name(symbol, ticker) if needs_name else None
        db.update_watchlist_sync_time(symbol, name=name)

    return {
        "symbol": symbol,
        "written": written,
        "errors": errors,
    }


def _resample_daily_bars(
    daily_bars: list[dict[str, Any]], target_interval: str, market: str
) -> list[dict[str, Any]]:
    """Aggregate daily OHLCV bars into weekly ('1w') or monthly ('1mo') bars."""
    if not daily_bars:
        return []

    df = pd.DataFrame(daily_bars)
    tz_name = "Asia/Taipei" if market == "TW" else "America/New_York"
    dt_index = pd.to_datetime(df["ts"], unit="s", utc=True).dt.tz_convert(tz_name)
    df.index = dt_index

    rule = "W-FRI" if target_interval == "1w" else "ME"
    agg = (
        df.resample(rule)
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
    )

    result: list[dict[str, Any]] = []
    for idx_ts, row in agg.iterrows():
        utc_date = idx_ts.tz_convert(timezone.utc)
        result.append(
            {
                "ts": int(utc_date.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
        )
    return result


def get_bars_for_chart(symbol: str, interval: str, limit: int = 1500) -> list[dict[str, Any]]:
    """Return bars for any supported timeframe, auto-fetching if DB cache is empty."""
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval: {interval}")

    market = infer_market(symbol)
    if interval in ("1w", "1mo"):
        daily_bars = db.get_bars(symbol, "1d", limit=3000)
        if not daily_bars:
            fetch_and_store(symbol, intervals=["1d"], full=True)
            daily_bars = db.get_bars(symbol, "1d", limit=3000)
        resampled = _resample_daily_bars(daily_bars, interval, market)
        return resampled[-limit:]

    bars = db.get_bars(symbol, interval, limit=limit)
    if not bars:
        fetch_and_store(symbol, intervals=[interval], full=True)
        bars = db.get_bars(symbol, interval, limit=limit)
    return bars
