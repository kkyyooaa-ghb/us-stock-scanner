"""Evaluate saved V1.3 scanner snapshots without touching legacy Notion R.

Usage:
    python track_shadow_performance.py path/to/scan_result.csv [...]
    python track_shadow_performance.py snapshots/*.csv --output shadow_performance.csv

The input snapshots remain immutable.  This adapter downloads as-traded daily
bars and writes a separate measurement file that can be regenerated at any
time while the source snapshot is retained.
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys
from typing import Mapping

import pandas as pd

from execution_measurement import (
    MeasurementStatus,
    TradeMeasurement,
    evaluate_trade_plan,
)
from sources import ET_TZ
from trade_plan import OrderType, PlanAnchor, SignalLeg, TradePlan

try:
    import yfinance as yf
    HAS_YF = True
except ImportError:
    HAS_YF = False


IDENTITY_COLUMNS = (
    "SnapshotAsOfET",
    "DataBarDate",
    "Ticker",
    "SignalEngineVersion",
    "TradePlanVersion",
    "PlanMeasurementVersion",
    "ConfigHash",
    "UniverseVersion",
    "SelectedLeg",
    "OrderType",
    "PlanSelectedLeg",
    "PlanValidDays",
    "PlanTimeExitDays",
    "PriorityPreTheme",
    "PriorityPostTheme",
    "ThemeScore",
    "CrossedThresholdDueToTheme",
    "EnteredTop10DueToTheme",
    "PreGapPct",
    "MarketBias",
    "VixLevel",
    "SpyGapPct",
    "QqqGapPct",
    "BreadthPct",
    "SmallCapWeak",
)


def _optional_number(row: pd.Series, key: str) -> float | None:
    value = row.get(key)
    if value is None or pd.isna(value) or value == "":
        return None
    return float(value)


def _required_int(row: pd.Series, key: str) -> int:
    value = _optional_number(row, key)
    if value is None:
        raise ValueError(f"missing {key}")
    return int(value)


def _plan_from_snapshot(row: pd.Series) -> TradePlan:
    selected_leg = str(row.get("PlanSelectedLeg") or row.get("SelectedLeg") or "none")
    return TradePlan(
        status=str(row.get("TradePlanStatus") or ""),
        version=str(row.get("TradePlanVersion") or ""),
        measurement_version=str(row.get("PlanMeasurementVersion") or ""),
        selected_leg=SignalLeg(selected_leg),
        order_type=OrderType(str(row.get("OrderType") or "none")),
        anchor=PlanAnchor(str(row.get("PlanAnchor") or "none")),
        anchor_price=_optional_number(row, "PlanAnchorPrice"),
        trigger_price=_optional_number(row, "TriggerPrice"),
        entry_low=_optional_number(row, "PlanEntryLow"),
        entry_high=_optional_number(row, "PlanEntryHigh"),
        stop_loss=_optional_number(row, "PlanStopLoss"),
        valid_days=_required_int(row, "PlanValidDays"),
        time_exit_days=_required_int(row, "PlanTimeExitDays"),
        stop_type=str(row.get("PlanStopType") or ""),
        exit_rule=str(row.get("PlanExitRule") or ""),
        reason=str(row.get("PlanReason") or ""),
    )


def _scan_date(row: pd.Series) -> str:
    value = str(row.get("SnapshotAsOfET") or "")
    if len(value) < 10:
        raise ValueError("missing SnapshotAsOfET")
    return value[:10]


def evaluate_snapshot(
    snapshot: pd.DataFrame,
    histories: Mapping[str, pd.DataFrame],
) -> pd.DataFrame:
    """Evaluate all shadow-ready plans in one or more immutable snapshots."""
    if snapshot is None or snapshot.empty:
        return pd.DataFrame()

    eligible = snapshot[
        snapshot.get("TradePlanStatus", pd.Series(index=snapshot.index, dtype=str))
        == "shadow_ready"
    ].copy()
    if eligible.empty:
        return pd.DataFrame()

    dedupe_columns = [
        column
        for column in ("SnapshotAsOfET", "Ticker", "TradePlanVersion")
        if column in eligible.columns
    ]
    if dedupe_columns:
        eligible = eligible.drop_duplicates(dedupe_columns, keep="last")

    output = []
    for _, row in eligible.iterrows():
        ticker = str(row.get("Ticker") or "").strip().upper()
        identity = {
            column: row.get(column)
            for column in IDENTITY_COLUMNS
            if column in row.index
        }
        try:
            plan = _plan_from_snapshot(row)
            result = evaluate_trade_plan(
                _scan_date(row),
                histories.get(ticker, pd.DataFrame()),
                plan,
            )
        except Exception as exc:
            result = TradeMeasurement(
                status=MeasurementStatus.INVALID_PLAN,
                measurement_version=str(
                    row.get("PlanMeasurementVersion") or "v1.3.0-shadow"
                ),
                reason=f"snapshot_parse_error:{type(exc).__name__}:{exc}",
            )
        output.append({**identity, **result.to_snapshot_fields()})
    return pd.DataFrame(output)


def _history_for_ticker(data: pd.DataFrame, ticker: str) -> pd.DataFrame:
    fields = ("Open", "High", "Low", "Close", "Dividends", "Stock Splits")
    out = {}
    for field in fields:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if (field, ticker) in data.columns:
                    series = data[(field, ticker)]
                elif (ticker, field) in data.columns:
                    series = data[(ticker, field)]
                else:
                    continue
            elif field in data.columns:
                series = data[field]
            else:
                continue
            out[field] = series
        except Exception:
            continue
    return pd.DataFrame(out, index=data.index)


def download_as_traded_histories(
    tickers: list[str],
    earliest_scan_date: str,
) -> dict[str, pd.DataFrame]:
    """Download raw OHLC plus actions; never use auto-adjusted bars here."""
    data = yf.download(
        sorted(set(tickers)),
        start=earliest_scan_date,
        progress=False,
        auto_adjust=False,
        actions=True,
        repair=False,
        threads=True,
        group_by="column",
    )
    if data is None or data.empty:
        return {}

    histories = {}
    now_et = datetime.now(ET_TZ)
    for ticker in sorted(set(tickers)):
        frame = _history_for_ticker(data, ticker)
        if (
            not frame.empty
            and now_et.hour < 17
            and frame.index[-1].strftime("%Y-%m-%d") == now_et.strftime("%Y-%m-%d")
        ):
            frame = frame.iloc[:-1]
        histories[ticker] = frame
    return histories


def _load_snapshots(paths: list[str]) -> pd.DataFrame:
    frames = [pd.read_csv(path) for path in paths]
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate immutable V1.3 scanner snapshots."
    )
    parser.add_argument("snapshots", nargs="+", help="One or more scan_result CSV files")
    parser.add_argument(
        "--output",
        default="shadow_performance.csv",
        help="Output CSV (default: shadow_performance.csv)",
    )
    args = parser.parse_args(argv)

    if not HAS_YF:
        print("❌ yfinance 未安裝")
        return 1

    missing = [path for path in args.snapshots if not Path(path).is_file()]
    if missing:
        print(f"❌ 找不到 snapshot:{missing}")
        return 1

    snapshot = _load_snapshots(args.snapshots)
    if snapshot.empty or "Ticker" not in snapshot or "SnapshotAsOfET" not in snapshot:
        print("❌ snapshot 為空或缺 Ticker/SnapshotAsOfET")
        return 1

    if "TradePlanStatus" not in snapshot.columns:
        print("ℹ️  snapshots 尚無 V1.3 TradePlan 欄位")
        pd.DataFrame().to_csv(args.output, index=False, encoding="utf-8-sig")
        return 0

    plans = snapshot[snapshot["TradePlanStatus"] == "shadow_ready"]
    if plans.empty:
        print("ℹ️  snapshot 沒有 shadow-ready TradePlan")
        pd.DataFrame().to_csv(args.output, index=False, encoding="utf-8-sig")
        return 0

    tickers = sorted(set(plans["Ticker"].astype(str).str.upper()))
    earliest = min(plans["SnapshotAsOfET"].astype(str).str[:10])
    print(f"📐 V1.3 shadow 量尺:{len(plans)} 筆計畫 / {len(tickers)} 檔股票")
    print(f"  📡 as-traded OHLC + actions，自 {earliest} 起")
    histories = download_as_traded_histories(tickers, earliest)
    result = evaluate_snapshot(snapshot, histories)
    result.to_csv(args.output, index=False, encoding="utf-8-sig")

    counts = result.get("V13MeasurementStatus", pd.Series(dtype=str)).value_counts()
    summary = " | ".join(f"{key} {value}" for key, value in counts.items())
    print(f"✅ 輸出 {args.output}:{len(result)} 筆" + (f" ({summary})" if summary else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
