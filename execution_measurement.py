"""V1.3 shadow execution and R measurement.

The module exposes one interface, :func:`evaluate_trade_plan`.  Callers provide
an immutable TradePlan and unadjusted as-traded OHLC/actions.  Order semantics,
bar-order uncertainty, splits, dividends, time exits, horizons, MFE, and MAE
remain implementation details behind that seam.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from config import Config
from trade_plan import OrderType, TradePlan


class MeasurementStatus(str, Enum):
    INVALID_PLAN = "invalid_plan"
    NO_DATA = "no_data"
    AWAITING_FILL = "awaiting_fill"
    UNFILLED = "unfilled"
    INVALIDATED = "invalidated"
    OPEN = "open"
    COMPLETED = "completed"


@dataclass(frozen=True)
class TradeMeasurement:
    """Observable result of evaluating one plan against as-traded history."""

    status: MeasurementStatus
    measurement_version: str
    filled: bool = False
    fill_date: str | None = None
    fill_price: float | None = None
    exit_date: str | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    ambiguous: bool = False
    r_lower: float | None = None
    r_upper: float | None = None
    mark_r: float | None = None
    mfe_r: float | None = None
    mae_r: float | None = None
    d20_total_return_pct: float | None = None
    d40_total_return_pct: float | None = None
    d60_total_return_pct: float | None = None
    dividends_per_initial_share: float = 0.0
    ending_split_factor: float = 1.0
    bars_observed: int = 0
    entry_window_complete: bool = False
    reason: str = ""

    def to_snapshot_fields(self) -> dict[str, Any]:
        return {
            "V13MeasurementVersion": self.measurement_version,
            "V13MeasurementStatus": self.status.value,
            "V13Filled": int(self.filled),
            "V13FillDate": self.fill_date,
            "V13FillPrice": self.fill_price,
            "V13ExitDate": self.exit_date,
            "V13ExitPrice": self.exit_price,
            "V13ExitReason": self.exit_reason,
            "V13Ambiguous": int(self.ambiguous),
            "V13RLower": self.r_lower,
            "V13RUpper": self.r_upper,
            "V13MarkR": self.mark_r,
            "V13MFER": self.mfe_r,
            "V13MAER": self.mae_r,
            "D20TotalReturnPct": self.d20_total_return_pct,
            "D40TotalReturnPct": self.d40_total_return_pct,
            "D60TotalReturnPct": self.d60_total_return_pct,
            "V13DividendsPerInitialShare": self.dividends_per_initial_share,
            "V13EndingSplitFactor": self.ending_split_factor,
            "V13BarsObserved": self.bars_observed,
            "V13EntryWindowComplete": int(self.entry_window_complete),
            "V13MeasurementReason": self.reason,
        }


@dataclass(frozen=True)
class _PathOutcome:
    complete: bool
    exit_pos: int | None
    exit_price: float | None
    exit_reason: str
    r_value: float | None
    mark_r: float | None
    dividends: float
    observed_end_pos: int


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def _prepare_history(
    history: pd.DataFrame,
    scan_date: str,
) -> tuple[pd.DataFrame | None, str]:
    if history is None or history.empty:
        return None, "empty_history"

    source = history.copy()
    names = {str(column).strip().lower(): column for column in source.columns}
    required = ("open", "high", "low", "close")
    missing = [name for name in required if name not in names]
    if missing:
        return None, f"missing_columns:{','.join(missing)}"

    frame = pd.DataFrame(index=pd.to_datetime(source.index))
    for name in required:
        frame[name] = pd.to_numeric(source[names[name]], errors="coerce").to_numpy()
    for name in ("dividends", "stock splits"):
        if name in names:
            frame[name] = pd.to_numeric(
                source[names[name]], errors="coerce"
            ).fillna(0.0).to_numpy()
        else:
            frame[name] = 0.0

    if frame.index.tz is not None:
        frame.index = frame.index.tz_localize(None)
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()
    frame = frame.dropna(subset=list(required))

    target_date = pd.Timestamp(scan_date).date()
    positions = [
        position
        for position, timestamp in enumerate(frame.index)
        if timestamp.date() == target_date
    ]
    if not positions:
        return None, f"scan_date_missing:{scan_date}"
    frame = frame.iloc[positions[0]:].copy()

    factor = 1.0
    factors = []
    for split in frame["stock splits"].astype(float):
        if split > 0:
            factor *= split
        factors.append(factor)
    frame["_factor"] = factors
    for field in required:
        frame[f"_norm_{field}"] = frame[field].astype(float) * frame["_factor"]
    frame["_norm_dividend"] = (
        frame["dividends"].astype(float) * frame["_factor"]
    )
    return frame, ""


def _horizon_total_return(frame: pd.DataFrame, bars: int) -> float | None:
    if bars >= len(frame):
        return None
    base = float(frame["_norm_close"].iloc[0])
    if base <= 0:
        return None
    close = float(frame["_norm_close"].iloc[bars])
    dividends = float(frame["_norm_dividend"].iloc[1:bars + 1].sum())
    return _round((close + dividends - base) / base * 100, 2)


def _path_after_entry(
    frame: pd.DataFrame,
    *,
    fill_pos: int,
    fill_price: float,
    stop: float,
    time_exit_days: int,
) -> _PathOutcome:
    risk = fill_price - stop
    time_exit_pos = fill_pos + time_exit_days
    last_search_pos = min(time_exit_pos, len(frame) - 1)

    for pos in range(fill_pos + 1, last_search_pos + 1):
        open_price = float(frame["_norm_open"].iloc[pos])
        low_price = float(frame["_norm_low"].iloc[pos])
        if open_price <= stop:
            exit_price = open_price
            reason = "gap_through_stop"
        elif low_price <= stop:
            exit_price = stop
            reason = "initial_stop"
        else:
            continue

        dividends = float(
            frame["_norm_dividend"].iloc[fill_pos + 1:pos + 1].sum()
        )
        r_value = (exit_price + dividends - fill_price) / risk
        return _PathOutcome(
            complete=True,
            exit_pos=pos,
            exit_price=exit_price,
            exit_reason=reason,
            r_value=_round(r_value),
            mark_r=None,
            dividends=dividends,
            observed_end_pos=pos,
        )

    if time_exit_pos < len(frame):
        exit_price = float(frame["_norm_close"].iloc[time_exit_pos])
        dividends = float(
            frame["_norm_dividend"].iloc[
                fill_pos + 1:time_exit_pos + 1
            ].sum()
        )
        r_value = (exit_price + dividends - fill_price) / risk
        return _PathOutcome(
            complete=True,
            exit_pos=time_exit_pos,
            exit_price=exit_price,
            exit_reason="time_exit",
            r_value=_round(r_value),
            mark_r=None,
            dividends=dividends,
            observed_end_pos=time_exit_pos,
        )

    observed_end = len(frame) - 1
    dividends = float(
        frame["_norm_dividend"].iloc[fill_pos + 1:observed_end + 1].sum()
    )
    current_close = float(frame["_norm_close"].iloc[observed_end])
    mark_r = (current_close + dividends - fill_price) / risk
    return _PathOutcome(
        complete=False,
        exit_pos=None,
        exit_price=None,
        exit_reason="",
        r_value=None,
        mark_r=_round(mark_r),
        dividends=dividends,
        observed_end_pos=observed_end,
    )


def _excursions(
    frame: pd.DataFrame,
    *,
    fill_pos: int,
    end_pos: int,
    fill_price: float,
    stop: float,
) -> tuple[float, float]:
    risk = fill_price - stop
    highs = frame["_norm_high"].iloc[fill_pos:end_pos + 1]
    lows = frame["_norm_low"].iloc[fill_pos:end_pos + 1]
    mfe = (float(highs.max()) - fill_price) / risk
    mae = (float(lows.min()) - fill_price) / risk
    return _round(mfe), _round(mae)


def evaluate_trade_plan(
    scan_date: str,
    history: pd.DataFrame,
    plan: TradePlan,
) -> TradeMeasurement:
    """Evaluate one V1.3 TradePlan using unadjusted as-traded daily bars.

    Interface rules:
    - ``history`` must use raw OHLC. Optional ``Dividends`` and
      ``Stock Splits`` columns use yfinance action semantics.
    - A plan's entry window starts on ``scan_date`` and counts trading bars.
    - A buy-limit fills at the open when open <= limit, otherwise at the limit
      when the day's low reaches it.
    - A buy-stop fills at the open when open >= trigger, otherwise at the
      trigger when the day's high reaches it.
    - If an intraday buy-stop bar also reaches the stop, daily bars cannot
      reveal ordering; final outcomes are reported as an R interval.
    - D+20/40/60 are split- and dividend-aware total returns from scan close.
    """
    frame, history_error = _prepare_history(history, scan_date)
    common = {
        "measurement_version": Config.SHADOW_MEASUREMENT_VERSION,
    }
    if frame is None:
        return TradeMeasurement(
            status=MeasurementStatus.NO_DATA,
            reason=history_error,
            **common,
        )

    horizons = {
        "d20_total_return_pct": _horizon_total_return(frame, 20),
        "d40_total_return_pct": _horizon_total_return(frame, 40),
        "d60_total_return_pct": _horizon_total_return(frame, 60),
    }
    observed = {
        "bars_observed": len(frame),
        "ending_split_factor": _round(float(frame["_factor"].iloc[-1]), 6),
        **horizons,
    }

    if (
        plan.status != "shadow_ready"
        or plan.order_type is OrderType.NONE
        or plan.stop_loss is None
        or plan.stop_loss <= 0
        or plan.valid_days <= 0
        or plan.time_exit_days < 0
    ):
        return TradeMeasurement(
            status=MeasurementStatus.INVALID_PLAN,
            reason="plan_not_evaluable",
            **observed,
            **common,
        )

    stop = float(plan.stop_loss)
    if plan.order_type is OrderType.BUY_STOP_RECLAIM:
        if plan.trigger_price is None or float(plan.trigger_price) <= stop:
            return TradeMeasurement(
                status=MeasurementStatus.INVALID_PLAN,
                reason="trigger_must_exceed_stop",
                **observed,
                **common,
            )
        order_price = float(plan.trigger_price)
    elif plan.order_type is OrderType.BUY_LIMIT_ZONE:
        if plan.entry_high is None or float(plan.entry_high) <= stop:
            return TradeMeasurement(
                status=MeasurementStatus.INVALID_PLAN,
                reason="limit_must_exceed_stop",
                **observed,
                **common,
            )
        order_price = float(plan.entry_high)
    else:
        return TradeMeasurement(
            status=MeasurementStatus.INVALID_PLAN,
            reason=f"unsupported_order_type:{plan.order_type.value}",
            **observed,
            **common,
        )

    fill_pos = None
    fill_price = None
    intraday_stop_fill = False
    at_open = False
    entry_bars = min(plan.valid_days, len(frame))
    for pos in range(entry_bars):
        open_price = float(frame["_norm_open"].iloc[pos])
        high_price = float(frame["_norm_high"].iloc[pos])
        low_price = float(frame["_norm_low"].iloc[pos])

        if plan.order_type is OrderType.BUY_STOP_RECLAIM:
            if open_price >= order_price:
                fill_pos, fill_price, at_open = pos, open_price, True
            elif high_price >= order_price:
                fill_pos, fill_price = pos, order_price
                intraday_stop_fill = True
        else:
            if open_price <= order_price:
                fill_pos, fill_price, at_open = pos, open_price, True
            elif low_price <= order_price:
                fill_pos, fill_price = pos, order_price
        if fill_pos is not None:
            break

    entry_window_complete = len(frame) >= plan.valid_days
    if fill_pos is None:
        status = (
            MeasurementStatus.UNFILLED
            if entry_window_complete
            else MeasurementStatus.AWAITING_FILL
        )
        return TradeMeasurement(
            status=status,
            entry_window_complete=entry_window_complete,
            reason="entry_not_reached",
            **observed,
            **common,
        )

    fill_price = float(fill_price)
    fill_date = frame.index[fill_pos].strftime("%Y-%m-%d")
    if fill_price <= stop:
        return TradeMeasurement(
            status=MeasurementStatus.INVALIDATED,
            filled=True,
            fill_date=fill_date,
            fill_price=_round(fill_price),
            exit_date=fill_date,
            exit_price=_round(fill_price),
            exit_reason="entry_at_or_below_stop",
            entry_window_complete=entry_window_complete,
            reason="gap_open_invalidated_initial_risk",
            **observed,
            **common,
        )

    fill_bar_low = float(frame["_norm_low"].iloc[fill_pos])
    same_bar_stop = fill_bar_low <= stop
    ambiguous = bool(
        plan.order_type is OrderType.BUY_STOP_RECLAIM
        and intraday_stop_fill
        and not at_open
        and same_bar_stop
    )

    if same_bar_stop and not ambiguous:
        mfe_r, mae_r = _excursions(
            frame,
            fill_pos=fill_pos,
            end_pos=fill_pos,
            fill_price=fill_price,
            stop=stop,
        )
        return TradeMeasurement(
            status=MeasurementStatus.COMPLETED,
            filled=True,
            fill_date=fill_date,
            fill_price=_round(fill_price),
            exit_date=fill_date,
            exit_price=_round(stop),
            exit_reason="initial_stop",
            r_lower=-1.0,
            r_upper=-1.0,
            mfe_r=mfe_r,
            mae_r=mae_r,
            entry_window_complete=entry_window_complete,
            **observed,
            **common,
        )

    path = _path_after_entry(
        frame,
        fill_pos=fill_pos,
        fill_price=fill_price,
        stop=stop,
        time_exit_days=plan.time_exit_days,
    )
    mfe_r, mae_r = _excursions(
        frame,
        fill_pos=fill_pos,
        end_pos=path.observed_end_pos,
        fill_price=fill_price,
        stop=stop,
    )

    if ambiguous:
        r_values = [-1.0]
        if path.r_value is not None:
            r_values.append(path.r_value)
        return TradeMeasurement(
            status=(
                MeasurementStatus.COMPLETED
                if path.complete
                else MeasurementStatus.OPEN
            ),
            filled=True,
            fill_date=fill_date,
            fill_price=_round(fill_price),
            exit_reason="ambiguous_entry_stop_sequence",
            ambiguous=True,
            r_lower=_round(min(r_values)) if path.complete else -1.0,
            r_upper=_round(max(r_values)) if path.complete else None,
            mark_r=path.mark_r,
            mfe_r=mfe_r,
            mae_r=mae_r,
            dividends_per_initial_share=_round(path.dividends, 6) or 0.0,
            entry_window_complete=entry_window_complete,
            reason=(
                "daily_bar_cannot_order_intraday_trigger_and_stop"
                if path.complete
                else "upper_path_still_open"
            ),
            **observed,
            **common,
        )

    exit_date = (
        frame.index[path.exit_pos].strftime("%Y-%m-%d")
        if path.exit_pos is not None
        else None
    )
    return TradeMeasurement(
        status=(
            MeasurementStatus.COMPLETED
            if path.complete
            else MeasurementStatus.OPEN
        ),
        filled=True,
        fill_date=fill_date,
        fill_price=_round(fill_price),
        exit_date=exit_date,
        exit_price=_round(path.exit_price),
        exit_reason=path.exit_reason,
        r_lower=path.r_value,
        r_upper=path.r_value,
        mark_r=path.mark_r,
        mfe_r=mfe_r,
        mae_r=mae_r,
        dividends_per_initial_share=_round(path.dividends, 6) or 0.0,
        entry_window_complete=entry_window_complete,
        **observed,
        **common,
    )
