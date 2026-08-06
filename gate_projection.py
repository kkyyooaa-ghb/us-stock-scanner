"""V1.3 調參閘門到達日預估。

為什麼需要:
  週報目前只顯示 `collecting 2/60`,每週看到的都是同一句話,無從判斷該繼續等
  還是該重新檢視閘門設計。本模組把「還要多久」量化出來。

模型(刻意分成兩半,因為兩半的可信度差很多):

  A. 已知管線 —— **決定性**。已成交且仍 open 的 episode,其 fill 日與
     `PlanTimeExitDays` 都已知,time exit 何時到期可直接算,不需任何假設。
  B. 未來到達 —— **估計值**。用觀察到的每日新增 episode 率 × 成交率外推。

  cohort 第一天會把當時所有既有訊號一次折成新 episode(2026-07-28 為 25 筆,
  其後每日僅 3~4 筆),那是一次性 backlog,不是日常到達率 —— 必須排除,否則
  會把到達率高估 6 倍以上,預估日期嚴重樂觀。

已知偏誤(一律誠實標註,不藏在數字裡):
  - 提早停損會讓實際完成日**早於**本預估;本模型只排 time exit,故預估偏晚。
    是否要建停損 hazard 模型,要等完成樣本夠多才估得動(目前 n=2)。
  - `awaiting_fill` 的 episode 未計入管線 —— 它們可能成交也可能不成交,
    計入等於預設會成交。少算一點,同樣讓預估偏晚。
  - 到達率以少數幾個掃描日估計,信賴區間很寬,故一律附樂觀/保守區間。

**本模組只做資訊呈現,不授權任何調參。** 預估失敗絕不可讓閘門變成允許調參,
也不該讓閘門 fail-closed —— 授權判斷只看實際 completed-R。
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from config import Config

# 到達率低到接近 0 時,外推會給出天文數字。超過此上限即回報 beyond_horizon,
# 而不是印一個 2040 年的假日期。
_MAX_TRADING_DAYS = 1000

# 排除 cohort 首日 backlog 後,至少要這麼多個觀察日才敢估到達率。
_MIN_OBSERVED_SCAN_DAYS = 2


def _blocked(reason: str, detail: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "reason": reason}
    if detail:
        out["detail"] = detail
    return out


def _default_session_resolver(as_of: str, trading_days: int) -> str | None:
    """把「as_of 之後第 N 個交易日」換成日曆日期;無法解析時回 None。

    `exchange_calendars` 在本機開發環境常未安裝,故一律 fail-soft:拿不到
    日曆就只回報交易日數,不猜日期。
    """
    try:
        from sources import _get_nyse_calendar

        cal = _get_nyse_calendar()
        start = pd.Timestamp(as_of)
        # 交易日約佔日曆日 69%,乘 2 再加 30 天緩衝足以涵蓋
        end = start + pd.Timedelta(days=int(trading_days) * 2 + 30)
        sessions = pd.DatetimeIndex(cal.sessions_in_range(start, end))
        later = sessions[sessions > start]
        if len(later) >= trading_days:
            return later[trading_days - 1].strftime("%Y-%m-%d")
    except Exception:
        return None
    return None


def _first_day_reaching(
    threshold: int,
    *,
    completed_now: int,
    pipeline_remaining: list[int],
    arrivals_per_day: float,
    fill_rate: float,
    time_exit_days: int,
) -> int | None:
    """最早在第幾個交易日累積完成數達到 threshold;超過視界回 None。"""
    if completed_now >= threshold:
        return 0

    completions_per_day = arrivals_per_day * fill_rate
    ordered = sorted(pipeline_remaining)

    for day in range(1, _MAX_TRADING_DAYS + 1):
        # 管線:剩餘天數 <= day 者已到期
        from_pipeline = sum(1 for remaining in ordered if remaining <= day)
        # 未來到達:第 a 個交易日新增者,於 a + time_exit 完成,
        # 故到第 day 天為止,只有 a <= day - time_exit 的批次來得及完成
        arrival_window = max(0, day - time_exit_days)
        from_future = arrival_window * completions_per_day
        if completed_now + from_pipeline + from_future >= threshold:
            return day
    return None


def _milestone(
    label: str,
    threshold: int,
    *,
    completed_now: int,
    pipeline_remaining: list[int],
    arrivals_per_day: float,
    arrivals_rate_low: float,
    arrivals_rate_high: float,
    fill_rate: float,
    time_exit_days: int,
    as_of: str,
    session_resolver: Callable[[str, int], str | None],
) -> dict[str, Any]:
    def solve(rate: float) -> int | None:
        return _first_day_reaching(
            threshold,
            completed_now=completed_now,
            pipeline_remaining=pipeline_remaining,
            arrivals_per_day=max(rate, 0.0),
            fill_rate=fill_rate,
            time_exit_days=time_exit_days,
        )

    central = solve(arrivals_per_day)
    optimistic = solve(arrivals_rate_high)
    pessimistic = solve(arrivals_rate_low)

    def to_date(day: int | None) -> str | None:
        if day is None:
            return None
        if day == 0:
            return as_of
        return session_resolver(as_of, day)

    return {
        "label": label,
        "threshold": threshold,
        "remaining": max(threshold - completed_now, 0),
        "trading_days": central,
        "trading_days_optimistic": optimistic,
        "trading_days_pessimistic": pessimistic,
        "eta_date": to_date(central),
        "eta_date_optimistic": to_date(optimistic),
        "eta_date_pessimistic": to_date(pessimistic),
        "beyond_horizon": central is None,
    }


def project_gate_completion(
    episodes: pd.DataFrame,
    *,
    minimum_completed: int = Config.EPISODE_TUNING_MIN_COMPLETED,
    target_completed: int = Config.EPISODE_TUNING_TARGET,
    as_of: str | None = None,
    session_resolver: Callable[[str, int], str | None] | None = None,
) -> dict[str, Any]:
    """預估 completed-R 達到 minimum 與 target 的交易日數與日期。

    `session_resolver` 是日曆接縫,測試可注入假日曆;預設走 XNYS,拿不到
    日曆時只回交易日數不回日期。
    """
    resolver = session_resolver or _default_session_resolver

    required = {
        "EpisodeStatus",
        "EpisodeStartDate",
        "PlanTimeExitDays",
        "V13BarsObserved",
    }
    if episodes is None or episodes.empty:
        return _blocked("no_episodes")
    missing = required - set(episodes.columns)
    if missing:
        return _blocked("missing_columns", ",".join(sorted(missing)))

    status = episodes["EpisodeStatus"].astype(str)
    completed_now = int((status == "completed").sum())
    filled = int((status == "open").sum()) + completed_now
    unfilled = int((status == "unfilled").sum())

    as_of = as_of or str(episodes["EpisodeStartDate"].max())

    # --- 已知管線(決定性) ---
    open_rows = episodes.loc[status == "open"]
    time_exit_series = pd.to_numeric(
        episodes["PlanTimeExitDays"], errors="coerce"
    ).dropna()
    if time_exit_series.empty:
        return _blocked("no_time_exit_days")
    time_exit_days = int(time_exit_series.max())

    pipeline_remaining: list[int] = []
    for _, row in open_rows.iterrows():
        observed = pd.to_numeric(row.get("V13BarsObserved"), errors="coerce")
        plan_exit = pd.to_numeric(row.get("PlanTimeExitDays"), errors="coerce")
        if pd.isna(plan_exit):
            plan_exit = time_exit_days
        elapsed = 0 if pd.isna(observed) else int(observed)
        # 至少 1,已超期者視為下一個交易日到期
        pipeline_remaining.append(max(int(plan_exit) - elapsed, 1))

    # --- 未來到達(估計) ---
    starts = episodes["EpisodeStartDate"].astype(str).value_counts().sort_index()
    if len(starts) < _MIN_OBSERVED_SCAN_DAYS + 1:
        # +1 是因為要先丟掉 cohort 首日的 backlog fold
        return _blocked(
            "insufficient_history",
            f"scan_days={len(starts)}；排除 cohort 首日後不足 "
            f"{_MIN_OBSERVED_SCAN_DAYS} 天,無法估計到達率",
        )
    observed_starts = starts.iloc[1:]
    observed_days = len(observed_starts)
    total_arrivals = float(observed_starts.sum())
    arrivals_per_day = total_arrivals / observed_days

    # 區間用 Poisson 而非樣本標準差。掃描日只有個位數時,樣本 sd 會給出
    # 假性精確的 ±2 天;計數過程的真實不確定性由總事件數決定,
    # 故用 n ± 2√n(約 95%)換算回每日到達率。
    spread = 2.0 * (total_arrivals ** 0.5)
    arrivals_rate_high = (total_arrivals + spread) / observed_days
    arrivals_rate_low = max((total_arrivals - spread) / observed_days, 0.0)

    decided = filled + unfilled
    if decided == 0:
        return _blocked("no_decided_fills")
    fill_rate = filled / decided

    if arrivals_per_day <= 0:
        return _blocked("no_arrivals")

    milestones = {
        "minimum": _milestone(
            "minimum", minimum_completed,
            completed_now=completed_now,
            pipeline_remaining=pipeline_remaining,
            arrivals_per_day=arrivals_per_day,
            arrivals_rate_low=arrivals_rate_low,
            arrivals_rate_high=arrivals_rate_high,
            fill_rate=fill_rate,
            time_exit_days=time_exit_days,
            as_of=as_of,
            session_resolver=resolver,
        ),
        "target": _milestone(
            "target", target_completed,
            completed_now=completed_now,
            pipeline_remaining=pipeline_remaining,
            arrivals_per_day=arrivals_per_day,
            arrivals_rate_low=arrivals_rate_low,
            arrivals_rate_high=arrivals_rate_high,
            fill_rate=fill_rate,
            time_exit_days=time_exit_days,
            as_of=as_of,
            session_resolver=resolver,
        ),
    }

    confidence = "low" if observed_days < 10 else "medium"

    return {
        "ok": True,
        "as_of": as_of,
        "confidence": confidence,
        "basis": {
            "completed_now": completed_now,
            "cohort_first_scan_day": str(starts.index[0]),
            "observed_scan_days": observed_days,
            "backlog_episodes_excluded": int(starts.iloc[0]),
            "arrivals_per_scan_day": round(arrivals_per_day, 3),
            "arrivals_rate_low": round(arrivals_rate_low, 3),
            "arrivals_rate_high": round(arrivals_rate_high, 3),
            "fill_rate": round(fill_rate, 4),
            "completions_per_scan_day": round(arrivals_per_day * fill_rate, 3),
            "time_exit_trading_days": time_exit_days,
            "pipeline_open_episodes": len(pipeline_remaining),
        },
        "milestones": milestones,
        "caveats": [
            "只排 time exit;提早停損會讓實際日期早於本預估",
            "awaiting_fill 未計入管線,預估偏晚",
            "cohort 首日 backlog 已排除,不列入到達率",
            "區間為到達率的 Poisson 95%;成交率的二項不確定性未計入",
            "任何改動 ConfigHash 的調整都會讓 cohort 歸零,預估同步作廢",
        ],
    }
