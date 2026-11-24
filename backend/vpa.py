"""Volume Price Analysis (VPA) engine based on Anna Coulling principles."""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _classify_volume_grade(vol_ratio: float) -> str:
    """Classify volume relative to 20-period average."""
    if vol_ratio >= 2.0:
        return "ultra_high"
    if vol_ratio >= 1.3:
        return "high"
    if vol_ratio >= 0.7:
        return "normal"
    return "low"


def _detect_signal(row: pd.Series) -> tuple[str, str, str, str, str]:
    """Detect the highest-priority VPA anomaly/signal on a single bar.

    Returns: (signal_code, signal_label, signal_desc, marker_position, marker_shape)
    """
    vol_ratio = float(row["vol_ratio"])
    spread_ratio = float(row["spread_ratio"])
    close_pos = float(row["close_pos"])
    upper_wick_ratio = float(row["upper_wick_ratio"])
    lower_wick_ratio = float(row["lower_wick_ratio"])
    is_bull = bool(row["close"] >= row["open"])

    # 1. Climax volume + wide spread
    if vol_ratio >= 1.8 and spread_ratio >= 1.5:
        if is_bull and close_pos >= 0.7:
            return (
                "climax_bull",
                "爆量長紅",
                "量比與振幅同步放大且收於高檔，多方動能強勁（若處高檔需留意買盤高潮）。",
                "belowBar",
                "arrowUp",
            )
        if (not is_bull) and close_pos <= 0.3:
            return (
                "climax_bear",
                "爆量長黑",
                "放量長黑且收於低檔，賣壓沉重（若處低檔則可能是恐慌拋售高潮）。",
                "aboveBar",
                "arrowDown",
            )

    # 2. Effort vs Result anomaly (high volume, narrow spread)
    if vol_ratio >= 1.6 and spread_ratio <= 0.65:
        pos = "aboveBar" if is_bull else "belowBar"
        return (
            "effort_no_result",
            "量大價滯",
            "成交量顯著放大但價格振幅狹窄（努力無結果），暗示主力在關鍵價位吸籌或出貨阻擋。",
            pos,
            "circle",
        )

    # 3. Long wick anomalies under elevated volume
    if vol_ratio >= 1.3:
        if lower_wick_ratio >= 0.55:
            return (
                "stopping_volume",
                "下影止跌量",
                "高量伴隨長下影線，顯示低檔有買盤承接測試（Stopping Volume）。",
                "belowBar",
                "arrowUp",
            )
        if upper_wick_ratio >= 0.55:
            return (
                "buying_climax_rejection",
                "上影遇阻量",
                "高量伴隨長上影線，顯示高檔遭遇顯著供給拋壓（Buying Climax Rejection）。",
                "aboveBar",
                "arrowDown",
            )

    # 4. Low-volume tests (No Supply / No Demand)
    if vol_ratio <= 0.65 and spread_ratio <= 0.75:
        if lower_wick_ratio >= 0.40 and is_bull:
            return (
                "low_vol_test_supply",
                "縮量測底",
                "成交量明顯萎縮且下影收紅，浮額清洗完畢（Test of Supply）。",
                "belowBar",
                "circle",
            )
        if is_bull and spread_ratio <= 0.60 and close_pos >= 0.60:
            return (
                "no_demand",
                "無量上攻",
                "價格上漲但量能低迷（No Demand），需警惕續航力不足。",
                "aboveBar",
                "circle",
            )

    return ("", "", "", "", "")


def analyze_vpa(bars: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute VPA metrics, volume grades, and chart markers for OHLCV bars."""
    if not bars:
        return {
            "bars": [],
            "markers": [],
            "summary": {
                "bar_count": 0,
                "signal_counts": {},
                "latest": None,
            },
        }

    df = pd.DataFrame(bars)
    df["spread"] = (df["high"] - df["low"]).clip(lower=0.0)
    df["body"] = (df["close"] - df["open"]).abs()
    df["vol_ma20"] = df["volume"].rolling(window=20, min_periods=1).mean()
    df["spread_ma20"] = df["spread"].rolling(window=20, min_periods=1).mean()

    df["vol_ratio"] = np.where(df["vol_ma20"] > 0, df["volume"] / df["vol_ma20"], 1.0)
    df["spread_ratio"] = np.where(df["spread_ma20"] > 0, df["spread"] / df["spread_ma20"], 1.0)

    upper_wick = df["high"] - df[["open", "close"]].max(axis=1)
    lower_wick = df[["open", "close"]].min(axis=1) - df["low"]

    df["upper_wick_ratio"] = np.where(df["spread"] > 0, upper_wick / df["spread"], 0.0)
    df["lower_wick_ratio"] = np.where(df["spread"] > 0, lower_wick / df["spread"], 0.0)
    df["close_pos"] = np.where(df["spread"] > 0, (df["close"] - df["low"]) / df["spread"], 0.5)

    enriched_bars: list[dict[str, Any]] = []
    markers: list[dict[str, Any]] = []
    signal_counts: dict[str, int] = {}

    for idx, row in df.iterrows():
        vol_ratio = round(float(row["vol_ratio"]), 2)
        spread_ratio = round(float(row["spread_ratio"]), 2)
        grade = _classify_volume_grade(vol_ratio)
        # Skip signal detection on the very first 3 warmup bars
        if idx >= 3 and row["volume"] > 0:
            sig_code, sig_label, sig_desc, m_pos, m_shape = _detect_signal(row)
        else:
            sig_code, sig_label, sig_desc, m_pos, m_shape = ("", "", "", "", "")

        bar_ts = int(row["ts"])
        enriched_bars.append(
            {
                "time": bar_ts,
                "open": round(float(row["open"]), 4),
                "high": round(float(row["high"]), 4),
                "low": round(float(row["low"]), 4),
                "close": round(float(row["close"]), 4),
                "volume": float(row["volume"]),
                "vol_ratio": vol_ratio,
                "spread_ratio": spread_ratio,
                "volume_grade": grade,
                "signal": sig_code,
                "signal_label": sig_label,
                "signal_desc": sig_desc,
            }
        )

        if sig_code:
            signal_counts[sig_label] = signal_counts.get(sig_label, 0) + 1
            markers.append(
                {
                    "time": bar_ts,
                    "position": m_pos,
                    "shape": m_shape,
                    "signal": sig_code,
                    "text": sig_label,
                }
            )

    return {
        "bars": enriched_bars,
        "markers": markers,
        "summary": {
            "bar_count": len(enriched_bars),
            "signal_counts": signal_counts,
            "latest": enriched_bars[-1] if enriched_bars else None,
        },
    }
