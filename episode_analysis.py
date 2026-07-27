"""Build independent trade episodes and maturity KPIs from shadow signals.

The module exposes one interface, :func:`build_episode_analysis`.  Daily signal
deduplication, lifecycle grouping, stable IDs, segment statistics, and tuning
gates remain implementation details behind that seam.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any

import pandas as pd

from config import Config


@dataclass(frozen=True)
class EpisodeAnalysis:
    episodes: pd.DataFrame
    summary: dict[str, Any]
    markdown: str


def _text(row: pd.Series, *keys: str, default: str = "") -> str:
    for key in keys:
        value = row.get(key)
        if value is not None and not pd.isna(value) and str(value).strip():
            return str(value).strip()
    return default


def _date(value: Any) -> str | None:
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text[:10] if len(text) >= 10 else None


def _episode_boundary(row: pd.Series, signal_date: str) -> str | None:
    lifecycle_end = _date(row.get("V13LifecycleEndDate"))
    if lifecycle_end:
        return lifecycle_end

    status = _text(row, "V13MeasurementStatus")
    if status in {"open", "awaiting_fill"}:
        return None
    if status == "unfilled":
        return _date(row.get("V13EntryWindowEndDate")) or signal_date
    if status in {"completed", "invalidated"}:
        return (
            _date(row.get("V13ExitDate"))
            or _date(row.get("V13FillDate"))
            or signal_date
        )
    return signal_date


def _episode_id(row: pd.Series, signal_date: str) -> str:
    payload = "|".join([
        _text(row, "Ticker").upper(),
        _text(row, "SnapshotAsOfET", default=signal_date),
        _text(row, "TradePlanVersion"),
        _text(row, "PlanSelectedLeg", "SelectedLeg", default="none"),
    ])
    return f"ep-{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


def _finalize_episode(active: dict[str, Any]) -> dict[str, Any]:
    canonical = dict(active["canonical"])
    boundary = active["boundary"]
    status = str(canonical.get("V13MeasurementStatus") or "")
    canonical.update({
        "EpisodeId": active["episode_id"],
        "EpisodeStartDate": active["start_date"],
        "EpisodeEndDate": boundary,
        "EpisodeStatus": status,
        "EpisodeSignalCount": active["signal_count"],
        "EpisodeDuplicateSignals": active["signal_count"] - 1,
        "EpisodeLastSignalDate": active["last_signal_date"],
        "EpisodeObservedLegs": "|".join(sorted(active["observed_legs"])),
        "EpisodeObservedOrderTypes": "|".join(sorted(active["observed_orders"])),
        "EpisodeIsActive": int(boundary is None),
    })
    return canonical


def _build_episodes(performance: pd.DataFrame) -> pd.DataFrame:
    required = {"SnapshotAsOfET", "Ticker", "V13MeasurementStatus"}
    missing = required - set(performance.columns)
    if missing:
        raise ValueError(f"missing episode columns:{','.join(sorted(missing))}")

    frame = performance.copy()
    frame["_SignalDate"] = frame["SnapshotAsOfET"].map(_date)
    frame = frame[
        frame["_SignalDate"].notna()
        & frame["Ticker"].astype(str).str.strip().ne("")
    ].copy()
    if frame.empty:
        return pd.DataFrame()

    frame["_TickerKey"] = frame["Ticker"].astype(str).str.strip().str.upper()
    frame = frame.sort_values(
        ["_TickerKey", "_SignalDate", "SnapshotAsOfET"],
        kind="stable",
    )

    episodes: list[dict[str, Any]] = []
    for _, ticker_rows in frame.groupby("_TickerKey", sort=True):
        active = None
        for _, row in ticker_rows.iterrows():
            signal_date = str(row["_SignalDate"])
            if (
                active is not None
                and active["boundary"] is not None
                and signal_date > active["boundary"]
            ):
                episodes.append(_finalize_episode(active))
                active = None

            leg = _text(row, "PlanSelectedLeg", "SelectedLeg", default="none")
            order_type = _text(row, "OrderType", default="none")
            if active is None:
                canonical = row.drop(labels=["_SignalDate", "_TickerKey"]).to_dict()
                active = {
                    "canonical": canonical,
                    "episode_id": _episode_id(row, signal_date),
                    "start_date": signal_date,
                    "boundary": _episode_boundary(row, signal_date),
                    "signal_count": 1,
                    "last_signal_date": signal_date,
                    "observed_legs": {leg},
                    "observed_orders": {order_type},
                }
            else:
                active["signal_count"] += 1
                active["last_signal_date"] = signal_date
                active["observed_legs"].add(leg)
                active["observed_orders"].add(order_type)

        if active is not None:
            episodes.append(_finalize_episode(active))

    out = pd.DataFrame(episodes)
    if out.empty:
        return out
    episode_columns = [
        "EpisodeId",
        "EpisodeStartDate",
        "EpisodeEndDate",
        "EpisodeStatus",
        "EpisodeSignalCount",
        "EpisodeDuplicateSignals",
        "EpisodeLastSignalDate",
        "EpisodeObservedLegs",
        "EpisodeObservedOrderTypes",
        "EpisodeIsActive",
    ]
    remaining = [column for column in out.columns if column not in episode_columns]
    return out[episode_columns + remaining].sort_values(
        ["EpisodeStartDate", "Ticker"],
        kind="stable",
    ).reset_index(drop=True)


def _mean(series: pd.Series) -> float | None:
    values = pd.to_numeric(series, errors="coerce").dropna()
    return round(float(values.mean()), 4) if len(values) else None


def _metrics(episodes: pd.DataFrame) -> dict[str, Any]:
    n = len(episodes)
    if n == 0:
        return {
            "episodes": 0,
            "filled": 0,
            "unfilled": 0,
            "awaiting_fill": 0,
            "open": 0,
            "completed": 0,
            "completed_r": 0,
            "ambiguous": 0,
            "invalidated": 0,
            "invalid_plan": 0,
            "no_data": 0,
            "filled_rate": None,
            "unfilled_rate": None,
            "r_lower_mean": None,
            "r_upper_mean": None,
            "conservative_win_rate": None,
            "optimistic_win_rate": None,
            "mfe_r_mean": None,
            "mae_r_mean": None,
        }

    status = episodes["V13MeasurementStatus"].fillna("").astype(str)
    filled = pd.to_numeric(
        episodes.get("V13Filled", pd.Series(0, index=episodes.index)),
        errors="coerce",
    ).fillna(0).astype(int).eq(1)
    ambiguous = pd.to_numeric(
        episodes.get("V13Ambiguous", pd.Series(0, index=episodes.index)),
        errors="coerce",
    ).fillna(0).astype(int).eq(1)
    r_lower = pd.to_numeric(
        episodes.get("V13RLower", pd.Series(index=episodes.index, dtype=float)),
        errors="coerce",
    )
    r_upper = pd.to_numeric(
        episodes.get("V13RUpper", pd.Series(index=episodes.index, dtype=float)),
        errors="coerce",
    )
    completed_r = status.eq("completed") & r_lower.notna() & r_upper.notna()
    fill_denominator = int(filled.sum() + status.eq("unfilled").sum())

    return {
        "episodes": n,
        "filled": int(filled.sum()),
        "unfilled": int(status.eq("unfilled").sum()),
        "awaiting_fill": int(status.eq("awaiting_fill").sum()),
        "open": int(status.eq("open").sum()),
        "completed": int(status.eq("completed").sum()),
        "completed_r": int(completed_r.sum()),
        "ambiguous": int(ambiguous.sum()),
        "invalidated": int(status.eq("invalidated").sum()),
        "invalid_plan": int(status.eq("invalid_plan").sum()),
        "no_data": int(status.eq("no_data").sum()),
        "filled_rate": (
            round(float(filled.sum()) / fill_denominator, 4)
            if fill_denominator
            else None
        ),
        "unfilled_rate": (
            round(float(status.eq("unfilled").sum()) / fill_denominator, 4)
            if fill_denominator
            else None
        ),
        "r_lower_mean": _mean(r_lower[completed_r]),
        "r_upper_mean": _mean(r_upper[completed_r]),
        "conservative_win_rate": (
            round(float((r_lower[completed_r] > 0).mean()), 4)
            if completed_r.any()
            else None
        ),
        "optimistic_win_rate": (
            round(float((r_upper[completed_r] > 0).mean()), 4)
            if completed_r.any()
            else None
        ),
        "mfe_r_mean": _mean(
            episodes.loc[completed_r, "V13MFER"]
            if "V13MFER" in episodes
            else pd.Series(dtype=float)
        ),
        "mae_r_mean": _mean(
            episodes.loc[completed_r, "V13MAER"]
            if "V13MAER" in episodes
            else pd.Series(dtype=float)
        ),
    }


def _segments(
    episodes: pd.DataFrame,
    column: str,
    *,
    overall_ready: bool,
    segment_min_completed: int,
) -> list[dict[str, Any]]:
    if episodes.empty or column not in episodes:
        return []
    output = []
    labels = episodes[column].fillna("unknown").astype(str)
    for label, group in episodes.groupby(labels, sort=True):
        metrics = _metrics(group)
        output.append({
            "segment": label or "unknown",
            **metrics,
            "segment_min_completed": segment_min_completed,
            "tuning_ready": bool(
                overall_ready
                and metrics["completed_r"] >= segment_min_completed
            ),
        })
    return output


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _fmt_r(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}"


def _markdown(summary: dict[str, Any]) -> str:
    overall = summary["overall"]
    maturity = summary["maturity"]
    lines = [
        "# V1.3 Shadow Episode Report",
        "",
        f"- 原始日訊號：{summary['raw_signals']}",
        f"- 獨立 episodes：{summary['episodes']}",
        f"- 去除重複訊號：{summary['duplicate_signals']} "
        f"({_fmt_pct(summary['dedupe_rate'])})",
        f"- 已完成 R episodes：{overall['completed_r']}",
        f"- 調參閘門：{maturity['stage']} "
        f"({maturity['completed_r']}/{maturity['minimum_completed']} minimum；"
        f"target {maturity['target_completed']})",
        "",
        "## Lifecycle",
        "",
        "| Filled | Unfilled | Awaiting | Open | Completed R | Ambiguous |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {overall['filled']} | {overall['unfilled']} | "
        f"{overall['awaiting_fill']} | {overall['open']} | "
        f"{overall['completed_r']} | {overall['ambiguous']} |",
        "",
        f"成交率：{_fmt_pct(overall['filled_rate'])}；"
        f"未成交率：{_fmt_pct(overall['unfilled_rate'])}；"
        f"R 期望區間：{_fmt_r(overall['r_lower_mean'])} ～ "
        f"{_fmt_r(overall['r_upper_mean'])}。",
    ]

    for title, key in (
        ("By Selected Leg", "by_selected_leg"),
        ("By Order Type", "by_order_type"),
    ):
        lines.extend([
            "",
            f"## {title}",
            "",
            "| Segment | Episodes | Filled | Unfilled | Open | Completed R | "
            "R lower | R upper | Ready |",
            "|---|---:|---:|---:|---:|---:|---:|---:|:---:|",
        ])
        for row in summary[key]:
            lines.append(
                f"| {row['segment']} | {row['episodes']} | {row['filled']} | "
                f"{row['unfilled']} | {row['open']} | {row['completed_r']} | "
                f"{_fmt_r(row['r_lower_mean'])} | {_fmt_r(row['r_upper_mean'])} | "
                f"{'yes' if row['tuning_ready'] else 'no'} |"
            )

    lines.extend([
        "",
        "> 本報告只使用 v1.3.0-shadow episode；legacy-v0 不混入。"
        "閘門未通過前不得依此調整權重。",
        "",
    ])
    return "\n".join(lines)


def build_episode_analysis(
    performance: pd.DataFrame,
    *,
    minimum_completed: int = Config.EPISODE_TUNING_MIN_COMPLETED,
    target_completed: int = Config.EPISODE_TUNING_TARGET,
    segment_min_completed: int = Config.EPISODE_SEGMENT_MIN_COMPLETED,
) -> EpisodeAnalysis:
    """Collapse daily signals into lifecycle episodes and compute maturity KPIs."""
    raw_signals = 0 if performance is None else len(performance)
    if performance is None or performance.empty:
        episodes = pd.DataFrame()
    else:
        episodes = _build_episodes(performance)

    overall = _metrics(episodes)
    completed_r = overall["completed_r"]
    if completed_r >= target_completed:
        stage = "target_reached"
    elif completed_r >= minimum_completed:
        stage = "minimum_reached"
    else:
        stage = "collecting"
    overall_ready = completed_r >= minimum_completed

    leg_column = (
        "PlanSelectedLeg"
        if "PlanSelectedLeg" in episodes
        else "SelectedLeg"
    )
    summary = {
        "schema_version": "v1.3.0",
        "measurement_version": Config.SHADOW_MEASUREMENT_VERSION,
        "raw_signals": raw_signals,
        "episodes": len(episodes),
        "duplicate_signals": raw_signals - len(episodes),
        "dedupe_rate": (
            round((raw_signals - len(episodes)) / raw_signals, 4)
            if raw_signals
            else None
        ),
        "overall": overall,
        "maturity": {
            "completed_r": completed_r,
            "minimum_completed": minimum_completed,
            "target_completed": target_completed,
            "remaining_to_minimum": max(minimum_completed - completed_r, 0),
            "remaining_to_target": max(target_completed - completed_r, 0),
            "stage": stage,
            "parameter_tuning_allowed": overall_ready,
        },
        "by_selected_leg": _segments(
            episodes,
            leg_column,
            overall_ready=overall_ready,
            segment_min_completed=segment_min_completed,
        ),
        "by_order_type": _segments(
            episodes,
            "OrderType",
            overall_ready=overall_ready,
            segment_min_completed=segment_min_completed,
        ),
    }
    markdown = _markdown(summary)
    return EpisodeAnalysis(episodes=episodes, summary=summary, markdown=markdown)
