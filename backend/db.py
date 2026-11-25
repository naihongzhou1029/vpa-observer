"""SQLite persistence layer for VPA-Observer."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "vpa_observer.db"

DEFAULT_WATCHLIST: list[tuple[str, str, str]] = [
    ("2330.TW", "TW", "台積電"),
    ("2317.TW", "TW", "鴻海"),
    ("0050.TW", "TW", "元大台灣50"),
    ("NVDA", "US", "NVIDIA"),
    ("AAPL", "US", "Apple"),
    ("SPY", "US", "SPDR S&P 500"),
]

VALID_TAGS: tuple[str, ...] = ("red", "yellow", "green")


def _parse_tags(raw: str | None) -> list[str]:
    """Parse stored comma-separated tag string into ordered valid tag list."""
    if not raw:
        return []
    seen = {part.strip().lower() for part in raw.split(",") if part.strip()}
    return [t for t in VALID_TAGS if t in seen]


def _serialize_tags(tags: list[str]) -> str:
    """Normalize and serialize tag list into canonical comma-separated string."""
    seen = {str(t).strip().lower() for t in tags if str(t).strip()}
    return ",".join(t for t in VALID_TAGS if t in seen)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a configured SQLite connection with WAL mode enabled."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    """Initialize database schema and seed default watchlist if empty."""
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS watchlist (
                symbol TEXT PRIMARY KEY,
                market TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                last_synced_at TEXT,
                tags TEXT NOT NULL DEFAULT ''
            );
            """
        )
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(watchlist)").fetchall()
        }
        if "tags" not in cols:
            conn.execute(
                "ALTER TABLE watchlist ADD COLUMN tags TEXT NOT NULL DEFAULT '';"
            )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS ohlcv (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                ts INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                PRIMARY KEY (symbol, interval, ts)
            );
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_ohlcv_lookup
            ON ohlcv (symbol, interval, ts DESC);
            """
        )
        existing_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        if existing_count == 0:
            now = utc_now_iso()
            conn.executemany(
                """
                INSERT OR IGNORE INTO watchlist (symbol, market, name, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [(sym, mkt, name, now) for sym, mkt, name in DEFAULT_WATCHLIST],
            )


def get_watchlist() -> list[dict[str, Any]]:
    """Return watchlist items enriched with latest daily close and change percent."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT symbol, market, name, created_at, last_synced_at, tags
            FROM watchlist
            ORDER BY CASE WHEN market = 'TW' THEN 0 ELSE 1 END, symbol ASC
            """
        ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["tags"] = _parse_tags(item.get("tags"))
            latest_bars = conn.execute(
                """
                SELECT close, ts FROM ohlcv
                WHERE symbol = ? AND interval = '1d'
                ORDER BY ts DESC
                LIMIT 2
                """,
                (item["symbol"],),
            ).fetchall()
            if latest_bars:
                last_close = float(latest_bars[0]["close"])
                item["last_close"] = round(last_close, 4)
                item["last_ts"] = int(latest_bars[0]["ts"])
                if len(latest_bars) > 1 and latest_bars[1]["close"] > 0:
                    prev_close = float(latest_bars[1]["close"])
                    item["change_pct"] = round(
                        ((last_close - prev_close) / prev_close) * 100.0, 2
                    )
                else:
                    item["change_pct"] = 0.0
            else:
                item["last_close"] = None
                item["last_ts"] = None
                item["change_pct"] = None
            items.append(item)
        return items


def get_watchlist_item(symbol: str) -> dict[str, Any] | None:
    """Return a single watchlist entry if present."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT symbol, market, name, created_at, last_synced_at, tags FROM watchlist WHERE symbol = ?",
            (symbol,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["tags"] = _parse_tags(item.get("tags"))
        return item


def upsert_watchlist_item(symbol: str, market: str, name: str = "") -> dict[str, Any]:
    """Insert or update a watchlist item."""
    now = utc_now_iso()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO watchlist (symbol, market, name, created_at, tags)
            VALUES (?, ?, ?, ?, '')
            ON CONFLICT(symbol) DO UPDATE SET
                market = excluded.market,
                name = CASE WHEN excluded.name != '' THEN excluded.name ELSE watchlist.name END
            """,
            (symbol, market, name, now),
        )
    return get_watchlist_item(symbol) or {
        "symbol": symbol,
        "market": market,
        "name": name,
        "created_at": now,
        "last_synced_at": None,
        "tags": [],
    }


def update_watchlist_tags(symbol: str, tags: list[str]) -> dict[str, Any] | None:
    """Update color tags for a watchlist item and return the updated entry."""
    serialized = _serialize_tags(tags)
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE watchlist SET tags = ? WHERE symbol = ?",
            (serialized, symbol),
        )
        if cur.rowcount == 0:
            return None
    return get_watchlist_item(symbol)


def delete_watchlist_item(symbol: str) -> bool:
    """Remove a symbol from the watchlist."""
    with get_conn() as conn:
        cur = conn.execute("DELETE FROM watchlist WHERE symbol = ?", (symbol,))
        return cur.rowcount > 0


def update_watchlist_name(symbol: str, name: str) -> None:
    """Update display name for a watchlist symbol."""
    if not name:
        return
    with get_conn() as conn:
        conn.execute(
            "UPDATE watchlist SET name = ? WHERE symbol = ?",
            (name, symbol),
        )


def update_watchlist_sync_time(symbol: str, name: str | None = None) -> None:
    """Update last_synced_at and optional display name for a symbol."""
    now = utc_now_iso()
    with get_conn() as conn:
        if name:
            conn.execute(
                """
                UPDATE watchlist
                SET last_synced_at = ?,
                    name = ?
                WHERE symbol = ?
                """,
                (now, name, symbol),
            )
        else:
            conn.execute(
                "UPDATE watchlist SET last_synced_at = ? WHERE symbol = ?",
                (now, symbol),
            )


def upsert_bars(symbol: str, interval: str, bars: list[dict[str, Any]]) -> int:
    """Upsert OHLCV bars for a symbol and interval, returning rows written."""
    if not bars:
        return 0
    records = [
        (
            symbol,
            interval,
            int(b["ts"]),
            float(b["open"]),
            float(b["high"]),
            float(b["low"]),
            float(b["close"]),
            float(b["volume"]),
        )
        for b in bars
    ]
    with get_conn() as conn:
        conn.executemany(
            """
            INSERT INTO ohlcv (symbol, interval, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, interval, ts) DO UPDATE SET
                open = excluded.open,
                high = excluded.high,
                low = excluded.low,
                close = excluded.close,
                volume = excluded.volume
            """,
            records,
        )
    return len(records)


def get_bars(symbol: str, interval: str, limit: int = 2000) -> list[dict[str, Any]]:
    """Fetch OHLCV bars in ascending chronological order."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT ts, open, high, low, close, volume
            FROM (
                SELECT ts, open, high, low, close, volume
                FROM ohlcv
                WHERE symbol = ? AND interval = ?
                ORDER BY ts DESC
                LIMIT ?
            )
            ORDER BY ts ASC
            """,
            (symbol, interval, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def has_bars(symbol: str, interval: str) -> bool:
    """Check whether any bars exist in SQLite for symbol and interval."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM ohlcv WHERE symbol = ? AND interval = ? LIMIT 1",
            (symbol, interval),
        ).fetchone()
        return row is not None


def get_db_stats() -> dict[str, Any]:
    """Return database summary metrics."""
    with get_conn() as conn:
        watchlist_count = conn.execute("SELECT COUNT(*) FROM watchlist").fetchone()[0]
        total_bars = conn.execute("SELECT COUNT(*) FROM ohlcv").fetchone()[0]
        by_interval = conn.execute(
            "SELECT interval, COUNT(*) AS cnt FROM ohlcv GROUP BY interval"
        ).fetchall()
        return {
            "db_path": str(DB_PATH),
            "watchlist_count": watchlist_count,
            "total_bars": total_bars,
            "bars_by_interval": {r["interval"]: r["cnt"] for r in by_interval},
        }
