"""Canonical contract for immutable scanner CSV snapshots.

All production, empty, and failure artifacts cross this module's writer
interface.  Callers never own header strings or CSV escaping rules.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import math
from pathlib import Path
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from config import Config


ET_TZ = ZoneInfo("America/New_York")


SNAPSHOT_COLUMNS = (
    "SnapshotSchemaVersion",
    "SnapshotRecordType",
    "SnapshotRunStatus",
    "SnapshotErrorType",
    "SnapshotErrorMessage",
    "Ticker",
    "Price",
    "MA20",
    "MA60",
    "VolRatio",
    "Support",
    "Resistance",
    "Position",
    "Status",
    "Priority",
    "Score",
    "PriorityPreTheme",
    "PriorityPostTheme",
    "ScorePreTheme",
    "RankScore",
    "RevenueScore",
    "ThemeScore",
    "EligiblePreTheme",
    "EligiblePostTheme",
    "CrossedThresholdDueToTheme",
    "EnteredTop10DueToTheme",
    "ConsecDays",
    "DistMA60Pct",
    "DistTag",
    "EntryLow",
    "EntryHigh",
    "StopLoss",
    "ATR",
    "ATR_Pct",
    "DistDirection",
    "DistATRMult",
    "YoY",
    "PreGapPct",
    "PreGapStatus",
    "SignalEngineVersion",
    "MeasurementVersion",
    "GitCommitSha",
    "ConfigHash",
    "UniverseVersion",
    "SnapshotAsOfET",
    "DataBarDate",
    "ScanSession",
    "ScanAfterOpen",
    "SnapshotTimingSource",
    "CandidateLeg",
    "SelectedLeg",
    "LegScoreRaw",
    "LegAnchor",
    "LegAnchorPrice",
    "VetoReason",
    "MarketBias",
    "VixLevel",
    "SpyPrice",
    "SpyPrevClose",
    "SpyGapPct",
    "QqqPrice",
    "QqqPrevClose",
    "QqqGapPct",
    "EsFuturesPct",
    "NqFuturesPct",
    "BreadthPct",
    "SmallCapWeak",
    "RSI",
    "VolDry",
    "NearMA60",
    "Oversold",
    "RsiTurnUp",
    "HoldMA",
    "SetupType",
    "DiagnosticSetupTypeV1",
    "TradePlanStatus",
    "TradePlanVersion",
    "PlanMeasurementVersion",
    "PlanSelectedLeg",
    "OrderType",
    "PlanAnchor",
    "PlanAnchorPrice",
    "TriggerPrice",
    "PlanEntryLow",
    "PlanEntryHigh",
    "PlanStopLoss",
    "PlanValidDays",
    "PlanTimeExitDays",
    "PlanStopType",
    "PlanExitRule",
    "PlanEarliestEntryDate",
    "PlanReason",
)


def snapshot_data_rows(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Return only stock data rows from canonical or legacy snapshots."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    if "SnapshotRecordType" in frame.columns:
        return frame[
            frame["SnapshotRecordType"].astype(str).eq("data")
        ].copy()
    if "Ticker" in frame.columns:
        return frame[
            frame["Ticker"].astype(str).ne("ERROR")
        ].copy()
    return frame.copy()


def _blank(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return series.isna() | text.isin({"", "nan", "None", "<NA>"})


def _strict_dates(
    series: pd.Series,
    *,
    label: str,
) -> pd.Series:
    parsed = []
    for value in series:
        text = str(value).strip()
        try:
            result = date.fromisoformat(text)
            if result.isoformat() != text:
                raise ValueError
        except Exception as exc:
            raise ValueError(f"invalid {label}") from exc
        parsed.append(result)
    return pd.Series(parsed, index=series.index, dtype=object)


def _validate_snapshot_timestamps(series: pd.Series) -> None:
    for value in series:
        text = str(value).strip()
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if "T" not in text or parsed.utcoffset() is None:
                raise ValueError
        except Exception as exc:
            raise ValueError("invalid SnapshotAsOfET") from exc


def canonicalize_snapshot(frame: pd.DataFrame | None) -> pd.DataFrame:
    """Validate and order a data snapshot without silently dropping fields."""
    source = pd.DataFrame() if frame is None else frame.copy()
    unknown = sorted(set(source.columns) - set(SNAPSHOT_COLUMNS))
    if unknown:
        raise ValueError(f"unknown snapshot columns:{','.join(unknown)}")

    if source.empty:
        return pd.DataFrame(columns=SNAPSHOT_COLUMNS)

    defaults: dict[str, Any] = {
        "SnapshotSchemaVersion": Config.SNAPSHOT_SCHEMA_VERSION,
        "SnapshotRecordType": "data",
        "SnapshotRunStatus": "ok",
        "SnapshotErrorType": "",
        "SnapshotErrorMessage": "",
    }
    for column, value in defaults.items():
        if column not in source:
            source[column] = value
        else:
            source[column] = source[column].fillna(value)

    if not source["SnapshotSchemaVersion"].astype(str).eq(
        Config.SNAPSHOT_SCHEMA_VERSION
    ).all():
        raise ValueError("snapshot schema version mismatch")

    record_type = source["SnapshotRecordType"].astype(str)
    invalid_record_types = sorted(set(record_type) - {"data", "control"})
    if invalid_record_types:
        raise ValueError(
            "invalid SnapshotRecordType:" + ",".join(invalid_record_types)
        )
    data_mask = record_type.eq("data")
    control_mask = record_type.eq("control")

    run_status = source["SnapshotRunStatus"].astype(str)
    if not run_status[data_mask].eq("ok").all():
        raise ValueError("data rows require ok SnapshotRunStatus")
    allowed_control_statuses = {"empty", "error", "skipped"}
    invalid_control_statuses = sorted(
        set(run_status[control_mask]) - allowed_control_statuses
    )
    if invalid_control_statuses:
        raise ValueError(
            "invalid control SnapshotRunStatus:"
            + ",".join(invalid_control_statuses)
        )

    if "SnapshotAsOfET" not in source.columns:
        raise ValueError("missing required snapshot columns:SnapshotAsOfET")
    _validate_snapshot_timestamps(source["SnapshotAsOfET"])

    if control_mask.any():
        if "SnapshotErrorType" not in source.columns:
            raise ValueError("control rows require SnapshotErrorType")
        if _blank(source.loc[control_mask, "SnapshotErrorType"]).any():
            raise ValueError("control rows require SnapshotErrorType")

    if data_mask.any():
        required_data_columns = {
            "Ticker",
            "Priority",
            "Score",
            "SnapshotAsOfET",
            "DataBarDate",
            "ScanSession",
            "ScanAfterOpen",
            "SnapshotTimingSource",
            "PreGapStatus",
            "SignalEngineVersion",
            "MeasurementVersion",
            "PlanSelectedLeg",
            "OrderType",
            "PlanAnchor",
            "PlanAnchorPrice",
            "TradePlanStatus",
            "TradePlanVersion",
            "PlanMeasurementVersion",
            "PlanEarliestEntryDate",
        }
        missing = sorted(required_data_columns - set(source.columns))
        if missing:
            raise ValueError(
                f"missing required snapshot columns:{','.join(missing)}"
            )

        if _blank(source.loc[data_mask, "Ticker"]).any():
            raise ValueError("data rows require Ticker")
        for score_column in ("Priority", "Score"):
            score_values = pd.to_numeric(
                source.loc[data_mask, score_column],
                errors="coerce",
            )
            if (
                score_values.isna().any()
                or not score_values.map(math.isfinite).all()
            ):
                raise ValueError(
                    f"{score_column} must contain finite numeric values"
                )
        _strict_dates(
            source.loc[data_mask, "DataBarDate"],
            label="DataBarDate",
        )
        scan_after_open = pd.to_numeric(
            source.loc[data_mask, "ScanAfterOpen"],
            errors="coerce",
        )
        if (
            scan_after_open.isna().any()
            or not scan_after_open.isin({0, 1}).all()
        ):
            raise ValueError("ScanAfterOpen must be 0 or 1")
        allowed_scan_sessions = {
            "preopen",
            "premarket",
            "after_open",
            "non_session",
            "calendar_error",
        }
        scan_sessions = source.loc[data_mask, "ScanSession"].astype(str)
        invalid_scan_sessions = sorted(
            set(scan_sessions) - allowed_scan_sessions
        )
        if invalid_scan_sessions:
            raise ValueError(
                "invalid ScanSession:" + ",".join(invalid_scan_sessions)
            )
        timing_sources = source.loc[
            data_mask,
            "SnapshotTimingSource",
        ].astype(str)
        if not timing_sources.isin({"xnys", "unavailable"}).all():
            raise ValueError("invalid SnapshotTimingSource")

        expected_versions = {
            "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
            "MeasurementVersion": Config.MEASUREMENT_VERSION,
            "TradePlanVersion": Config.TRADE_PLAN_VERSION,
            "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        }
        for column, expected in expected_versions.items():
            if not source.loc[data_mask, column].astype(str).eq(expected).all():
                raise ValueError(f"{column} mismatch")

        allowed_gap_statuses = {
            "available",
            "no_premarket_trade",
            "fetch_error",
            "outside_premarket_window",
            "disabled",
        }
        gap_status = source.loc[data_mask, "PreGapStatus"].astype(str)
        invalid_gap_status = sorted(
            set(gap_status) - allowed_gap_statuses
        )
        if invalid_gap_status:
            raise ValueError(
                "invalid PreGapStatus:" + ",".join(invalid_gap_status)
            )
        gap_pct = pd.to_numeric(
            source.get(
                "PreGapPct",
                pd.Series(index=source.index, dtype=float),
            ),
            errors="coerce",
        )
        if (
            gap_pct[data_mask].notna()
            & gap_status.ne("available")
        ).any():
            raise ValueError("PreGapPct requires available PreGapStatus")
        if (
            gap_status.eq("available")
            & gap_pct[data_mask].isna()
        ).any():
            raise ValueError("available PreGapStatus requires PreGapPct")

        allowed_plan_statuses = {
            "shadow_ready",
            "timing_unavailable",
            "not_applicable",
            "vetoed",
        }
        plan_status = source.loc[data_mask, "TradePlanStatus"].astype(str)
        invalid_plan_statuses = sorted(
            set(plan_status) - allowed_plan_statuses
        )
        if invalid_plan_statuses:
            raise ValueError(
                "invalid TradePlanStatus:" + ",".join(invalid_plan_statuses)
            )

        ready_mask = (
            data_mask
            & source["TradePlanStatus"].astype(str).eq("shadow_ready")
        )
        selected_plan_mask = (
            data_mask
            & source["TradePlanStatus"].astype(str).isin(
                {"shadow_ready", "timing_unavailable"}
            )
        )
        if selected_plan_mask.any():
            required_plan_columns = {
                "SelectedLeg",
                "PlanSelectedLeg",
                "LegAnchor",
                "PlanAnchor",
                "LegAnchorPrice",
                "PlanAnchorPrice",
                "OrderType",
                "TriggerPrice",
                "PlanEntryLow",
                "PlanEntryHigh",
                "PlanStopLoss",
                "PlanValidDays",
                "PlanTimeExitDays",
                "PlanStopType",
                "PlanExitRule",
            }
            missing_plan = sorted(required_plan_columns - set(source.columns))
            if missing_plan:
                raise ValueError(
                    "missing ready-plan columns:" + ",".join(missing_plan)
                )
            if not (
                source.loc[selected_plan_mask, "SelectedLeg"].astype(str).to_numpy()
                == source.loc[
                    selected_plan_mask,
                    "PlanSelectedLeg",
                ].astype(str).to_numpy()
            ).all():
                raise ValueError("selected leg mismatch")
            selected_legs = source.loc[
                selected_plan_mask,
                "SelectedLeg",
            ].astype(str)
            if not selected_legs.isin(
                {
                    "consolidation_dip",
                    "oversold_bounce",
                    "healthy_pullback",
                }
            ).all():
                raise ValueError("invalid executable selected leg")
            if not (
                source.loc[selected_plan_mask, "LegAnchor"].astype(str).to_numpy()
                == source.loc[
                    selected_plan_mask,
                    "PlanAnchor",
                ].astype(str).to_numpy()
            ).all():
                raise ValueError("anchor mismatch")
            selected_anchors = source.loc[
                selected_plan_mask,
                "PlanAnchor",
            ].astype(str)
            valid_anchor = (
                (
                    selected_legs.eq("consolidation_dip")
                    & selected_anchors.eq("ma60")
                )
                | (
                    selected_legs.eq("oversold_bounce")
                    & selected_anchors.eq("previous_high")
                )
                | (
                    selected_legs.eq("healthy_pullback")
                    & selected_anchors.isin({"ma20", "ma60"})
                )
            )
            if not valid_anchor.all():
                raise ValueError("selected leg and anchor mismatch")
            leg_prices = pd.to_numeric(
                source.loc[selected_plan_mask, "LegAnchorPrice"],
                errors="coerce",
            )
            plan_prices = pd.to_numeric(
                source.loc[selected_plan_mask, "PlanAnchorPrice"],
                errors="coerce",
            )
            if (
                leg_prices.isna().any()
                or plan_prices.isna().any()
                or (leg_prices <= 0).any()
                or (plan_prices <= 0).any()
                or ((leg_prices - plan_prices).abs() > 1e-9).any()
            ):
                raise ValueError("anchor price mismatch")

            numeric_fields = (
                "TriggerPrice",
                "PlanEntryLow",
                "PlanEntryHigh",
                "PlanStopLoss",
                "PlanValidDays",
                "PlanTimeExitDays",
            )
            numeric = {
                column: pd.to_numeric(
                    source.loc[selected_plan_mask, column],
                    errors="coerce",
                )
                for column in numeric_fields
            }
            if any(values.isna().any() for values in numeric.values()):
                raise ValueError("ready plan requires executable numeric fields")
            if (
                (numeric["TriggerPrice"] <= 0).any()
                or (numeric["PlanEntryLow"] <= 0).any()
                or (numeric["PlanEntryHigh"] <= 0).any()
                or (numeric["PlanStopLoss"] <= 0).any()
                or (numeric["PlanEntryLow"] > numeric["PlanEntryHigh"]).any()
                or (numeric["PlanStopLoss"] >= numeric["PlanEntryLow"]).any()
            ):
                raise ValueError("invalid executable plan prices")
            valid_days = numeric["PlanValidDays"]
            exit_days = numeric["PlanTimeExitDays"]
            if (
                (valid_days <= 0).any()
                or (valid_days % 1 != 0).any()
                or (exit_days < 0).any()
                or (exit_days % 1 != 0).any()
            ):
                raise ValueError("invalid executable plan days")
            order_types = source.loc[
                selected_plan_mask,
                "OrderType",
            ].astype(str)
            if not order_types.isin(
                {"buy_limit_zone", "buy_stop_reclaim"}
            ).all():
                raise ValueError("invalid executable OrderType")
            if (
                _blank(source.loc[selected_plan_mask, "PlanStopType"]).any()
                or _blank(source.loc[selected_plan_mask, "PlanExitRule"]).any()
            ):
                raise ValueError("ready plan requires stop and exit rules")

            buy_stop_mask = (
                selected_plan_mask
                & source["OrderType"].astype(str).eq("buy_stop_reclaim")
            )
            if buy_stop_mask.any():
                buy_stop_trigger = pd.to_numeric(
                    source.loc[buy_stop_mask, "TriggerPrice"],
                    errors="coerce",
                )
                buy_stop_stop = pd.to_numeric(
                    source.loc[buy_stop_mask, "PlanStopLoss"],
                    errors="coerce",
                )
                if (buy_stop_trigger <= buy_stop_stop).any():
                    raise ValueError("buy-stop trigger must exceed stop")

        if ready_mask.any():
            earliest_dates = _strict_dates(
                source.loc[ready_mask, "PlanEarliestEntryDate"],
                label="PlanEarliestEntryDate",
            )
            snapshot_dates = pd.Series(
                [
                    date.fromisoformat(str(value)[:10])
                    for value in source.loc[ready_mask, "SnapshotAsOfET"]
                ],
                index=earliest_dates.index,
                dtype=object,
            )
            if (earliest_dates < snapshot_dates).any():
                raise ValueError("PlanEarliestEntryDate precedes snapshot")

        timing_unavailable_mask = (
            data_mask
            & source["TradePlanStatus"].astype(str).eq("timing_unavailable")
        )
        if (
            timing_unavailable_mask.any()
            and not _blank(
                source.loc[
                    timing_unavailable_mask,
                    "PlanEarliestEntryDate",
                ]
            ).all()
        ):
            raise ValueError("timing_unavailable plan must not guess entry date")

        no_plan_mask = (
            data_mask
            & source["TradePlanStatus"].astype(str).isin(
                {"not_applicable", "vetoed"}
            )
        )
        if no_plan_mask.any():
            no_plan_fields = {
                "SelectedLeg": "none",
                "PlanSelectedLeg": "none",
                "OrderType": "none",
                "PlanAnchor": "none",
            }
            for column, expected in no_plan_fields.items():
                if not source.loc[
                    no_plan_mask,
                    column,
                ].astype(str).eq(expected).all():
                    raise ValueError(f"{column} must be {expected}")
            plan_anchor_prices = source.get(
                "PlanAnchorPrice",
                pd.Series(pd.NA, index=source.index),
            )
            if not _blank(plan_anchor_prices.loc[no_plan_mask]).all():
                raise ValueError("non-executable plan anchor price must be blank")

    for column in SNAPSHOT_COLUMNS:
        if column not in source:
            source[column] = pd.NA
    return source.loc[:, SNAPSHOT_COLUMNS]


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            temp_path = Path(handle.name)
            frame.to_csv(handle, index=False)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()


def write_snapshot(frame: pd.DataFrame, path: str | Path) -> Path:
    """Atomically write normal scanner rows with the canonical schema."""
    destination = Path(path)
    if frame is None or frame.empty:
        return write_control_snapshot(
            destination,
            status="empty",
            control_type="EmptyResult",
            message="scanner_completed_without_data_rows",
            scan_session="unknown",
        )
    _atomic_write_csv(canonicalize_snapshot(frame), destination)
    return destination


def write_control_snapshot(
    path: str | Path,
    *,
    status: str,
    control_type: str,
    message: str,
    scan_session: str,
    snapshot_as_of_et: str | None = None,
) -> Path:
    """Write one typed non-data artifact through the canonical contract."""
    if status not in {"empty", "error", "skipped"}:
        raise ValueError(f"unsupported control status:{status}")
    timestamp = snapshot_as_of_et or datetime.now(ET_TZ).isoformat()
    control = pd.DataFrame([{
        "SnapshotSchemaVersion": Config.SNAPSHOT_SCHEMA_VERSION,
        "SnapshotRecordType": "control",
        "SnapshotRunStatus": status,
        "SnapshotErrorType": control_type,
        "SnapshotErrorMessage": str(message),
        "SnapshotAsOfET": timestamp,
        "ScanSession": scan_session,
        "ScanAfterOpen": pd.NA,
        "SnapshotTimingSource": "unavailable",
        "PreGapStatus": "not_attempted",
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "MeasurementVersion": Config.MEASUREMENT_VERSION,
        "TradePlanVersion": Config.TRADE_PLAN_VERSION,
        "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "TradePlanStatus": "not_applicable",
    }])
    destination = Path(path)
    _atomic_write_csv(canonicalize_snapshot(control), destination)
    return destination


def write_skipped_snapshot(
    path: str | Path,
    reason: str,
    *,
    skip_type: str = "ScannerSkipped",
    snapshot_as_of_et: str | None = None,
) -> Path:
    """Write a successful, intentional no-scan artifact."""
    return write_control_snapshot(
        path,
        status="skipped",
        control_type=skip_type,
        message=reason,
        scan_session="non_session",
        snapshot_as_of_et=snapshot_as_of_et,
    )


def write_failure_snapshot(
    path: str | Path,
    error: str,
    *,
    error_type: str = "ScannerFailure",
    snapshot_as_of_et: str | None = None,
) -> Path:
    """Atomically write one typed control row using the canonical schema."""
    return write_control_snapshot(
        path,
        status="error",
        control_type=error_type,
        message=error,
        scan_session="error",
        snapshot_as_of_et=snapshot_as_of_et,
    )


def ensure_snapshot(
    path: str | Path,
    *,
    error: str = "scanner_did_not_create_output",
) -> bool:
    """Create a failure artifact only when the requested snapshot is missing."""
    destination = Path(path)
    if destination.is_file():
        return False
    write_failure_snapshot(
        destination,
        error,
        error_type="MissingOutput",
    )
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage canonical scan snapshots.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    ensure_parser = subparsers.add_parser(
        "ensure",
        help="Create a canonical failure snapshot only when output is missing.",
    )
    ensure_parser.add_argument("path")
    ensure_parser.add_argument(
        "--error",
        default="scanner_did_not_create_output",
    )
    args = parser.parse_args(argv)

    if args.command == "ensure":
        created = ensure_snapshot(args.path, error=args.error)
        print("created canonical failure snapshot" if created else "snapshot exists")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
