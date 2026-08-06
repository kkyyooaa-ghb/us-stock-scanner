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
from trade_plan import strategy_config_hash
from gate_projection import project_gate_completion


@dataclass(frozen=True)
class EpisodeAnalysis:
    episodes: pd.DataFrame
    summary: dict[str, Any]
    markdown: str


EPISODE_COLUMNS = (
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
)


def _empty_episode_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=EPISODE_COLUMNS)


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
        return _empty_episode_frame()

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
        return _empty_episode_frame()
    remaining = [column for column in out.columns if column not in EPISODE_COLUMNS]
    return out[list(EPISODE_COLUMNS) + remaining].sort_values(
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


def _current_measurement_rows(
    performance: pd.DataFrame | None,
) -> tuple[pd.DataFrame, int]:
    """Fail closed to the exact selection + measurement generation under review.

    A cohort is ``(SignalEngineVersion, ConfigHash)`` on the selection side and
    the plan/measurement version triple on the measurement side.  Two selection
    universes must never share one tuning gate, however identical their plan
    mechanics look — that is the accident this project has already had once.
    """
    if performance is None or performance.empty:
        return pd.DataFrame(), 0

    required_versions = {
        "TradePlanVersion": Config.TRADE_PLAN_VERSION,
        "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "V13MeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "ConfigHash": strategy_config_hash(),
    }
    if not set(required_versions).issubset(performance.columns):
        return performance.iloc[0:0].copy(), len(performance)

    current = pd.Series(True, index=performance.index)
    for column, expected in required_versions.items():
        current &= performance[column].astype(str).eq(expected)

    return performance.loc[current].copy(), int((~current).sum())


def _fmt_pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _fmt_r(value: float | None) -> str:
    return "-" if value is None else f"{value:+.2f}"


def _projection_markdown(projection: dict[str, Any] | None) -> list[str]:
    """達標預估區塊。無法預估時明說原因,不留白也不假裝有數字。"""
    if not projection:
        return []
    if not projection.get("ok"):
        return [
            "",
            "## 達標預估",
            "",
            f"無法預估：{projection.get('reason', 'unknown')}"
            + (f"（{projection['detail']}）" if projection.get("detail") else ""),
        ]

    basis = projection["basis"]
    lines = [
        "",
        "## 達標預估",
        "",
        f"- 基準日：{projection['as_of']}（信心度 {projection['confidence']}）",
        f"- 已完成 R：{basis['completed_now']}；決定性管線（已成交未了結）："
        f"{basis['pipeline_open_episodes']} 筆",
        f"- 每掃描日新增 episode：{basis['arrivals_per_scan_day']}"
        f"（Poisson 95% {basis['arrivals_rate_low']}～{basis['arrivals_rate_high']}）"
        f"，觀察 {basis['observed_scan_days']} 個掃描日",
        f"- 成交率 {basis['fill_rate']:.1%}；time exit "
        f"{basis['time_exit_trading_days']} 個交易日",
        f"- cohort 首日 backlog {basis['backlog_episodes_excluded']} 筆已排除，"
        "不列入到達率",
        "",
        "| 里程碑 | 門檻 | 還差 | 交易日 | 預估日期 | 樂觀 | 保守 |",
        "|---|---:|---:|---:|---|---|---|",
    ]
    for key, label in (("minimum", "最低"), ("target", "目標")):
        m = projection["milestones"][key]
        if m["beyond_horizon"]:
            lines.append(
                f"| {label} | {m['threshold']} | {m['remaining']} | "
                "超出預估視界 | - | - | - |"
            )
            continue
        lines.append(
            f"| {label} | {m['threshold']} | {m['remaining']} | "
            f"{m['trading_days']} | {m['eta_date'] or '-'} | "
            f"{m['eta_date_optimistic'] or '-'} | {m['eta_date_pessimistic'] or '-'} |"
        )

    lines.append("")
    for caveat in projection.get("caveats", []):
        lines.append(f"> ⚠️ {caveat}")
    return lines


def _markdown(summary: dict[str, Any]) -> str:
    overall = summary["overall"]
    maturity = summary["maturity"]
    lines = [
        "# V1.3 Shadow Episode Report",
        "",
        f"- 選股 cohort：{summary['selection_cohort']['signal_engine_version']}"
        f" / {summary['selection_cohort']['config_hash']}",
        f"- 輸入訊號：{summary['input_signals']}",
        f"- 排除非本版量尺：{summary['excluded_version_signals']}",
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

    lines.extend(_projection_markdown(summary.get("projection")))

    lines.extend([
        "",
        f"> 本報告只使用 {summary['measurement_version']} episode；"
        "legacy-v0 不混入。"
        "閘門未通過前不得依此調整權重。",
        "",
    ])
    return "\n".join(lines)


def _safe_projection(
    episodes: pd.DataFrame,
    *,
    minimum_completed: int,
    target_completed: int,
) -> dict[str, Any]:
    """閘門到達日預估。這是報告用資訊,不是授權依據 —— 絕不可讓它拋出例外
    而中斷週報,也絕不可讓它的失敗改變 parameter_tuning_allowed。"""
    try:
        return project_gate_completion(
            episodes,
            minimum_completed=minimum_completed,
            target_completed=target_completed,
        )
    except Exception as exc:  # noqa: BLE001 — 預估失敗必須降級而非中斷
        return {"ok": False, "reason": "projection_failed", "detail": str(exc)}


def build_episode_analysis(
    performance: pd.DataFrame,
    *,
    minimum_completed: int = Config.EPISODE_TUNING_MIN_COMPLETED,
    target_completed: int = Config.EPISODE_TUNING_TARGET,
    segment_min_completed: int = Config.EPISODE_SEGMENT_MIN_COMPLETED,
) -> EpisodeAnalysis:
    """Collapse daily signals into lifecycle episodes and compute maturity KPIs."""
    input_signals = 0 if performance is None else len(performance)
    current_performance, excluded_version_signals = _current_measurement_rows(
        performance
    )
    raw_signals = len(current_performance)
    if current_performance.empty:
        episodes = _empty_episode_frame()
    else:
        episodes = _build_episodes(current_performance)

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
        "schema_version": Config.SNAPSHOT_SCHEMA_VERSION,
        "trade_plan_version": Config.TRADE_PLAN_VERSION,
        "measurement_version": Config.SHADOW_MEASUREMENT_VERSION,
        "selection_cohort": {
            "signal_engine_version": Config.SIGNAL_ENGINE_VERSION,
            "config_hash": strategy_config_hash(),
        },
        "input_signals": input_signals,
        "excluded_version_signals": excluded_version_signals,
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
        # 純資訊:預估何時達標。預估失敗絕不可影響授權判斷,故整段包起來,
        # 任何例外都降級為 projection_failed,閘門本身照常運作。
        "projection": _safe_projection(
            episodes,
            minimum_completed=minimum_completed,
            target_completed=target_completed,
        ),
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
