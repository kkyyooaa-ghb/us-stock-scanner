"""V1.3 shadow TradePlan module.

This module is the seam between signal selection and trade measurement.
Production selection remains on the V1.2 engine while V1.3 plans are recorded
for review and forward-only validation.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import hashlib
import json
from typing import Any

from config import Config


class SignalLeg(str, Enum):
    NONE = "none"
    CONSOLIDATION_DIP = "consolidation_dip"
    OVERSOLD_BOUNCE = "oversold_bounce"
    HEALTHY_PULLBACK = "healthy_pullback"
    LEGACY_ACCUMULATION = "legacy_accumulation"


class PlanAnchor(str, Enum):
    NONE = "none"
    MA20 = "ma20"
    MA60 = "ma60"
    PREVIOUS_HIGH = "previous_high"


class OrderType(str, Enum):
    NONE = "none"
    BUY_LIMIT_ZONE = "buy_limit_zone"
    BUY_STOP_RECLAIM = "buy_stop_reclaim"


@dataclass(frozen=True)
class SignalDecision:
    """Structured result of the mutually exclusive V1.2 signal engine."""

    status: str
    base_priority: int
    candidate_leg: SignalLeg = SignalLeg.NONE
    selected_leg: SignalLeg = SignalLeg.NONE
    leg_score_raw: int = 0
    veto_reason: str = ""
    anchor: PlanAnchor = PlanAnchor.NONE
    anchor_price: float = 0.0

    def legacy_tuple(self) -> tuple[str, int]:
        return self.status, self.base_priority


@dataclass(frozen=True)
class TradePlan:
    """Immutable shadow plan captured with the daily scanner snapshot."""

    status: str
    version: str
    measurement_version: str
    selected_leg: SignalLeg
    order_type: OrderType
    anchor: PlanAnchor
    anchor_price: float | None
    trigger_price: float | None
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    valid_days: int
    time_exit_days: int
    stop_type: str
    exit_rule: str
    reason: str = ""

    def to_snapshot_fields(self) -> dict[str, Any]:
        return {
            "TradePlanStatus": self.status,
            "TradePlanVersion": self.version,
            "PlanSelectedLeg": self.selected_leg.value,
            "OrderType": self.order_type.value,
            "PlanAnchor": self.anchor.value,
            "PlanAnchorPrice": self.anchor_price,
            "TriggerPrice": self.trigger_price,
            "PlanEntryLow": self.entry_low,
            "PlanEntryHigh": self.entry_high,
            "PlanStopLoss": self.stop_loss,
            "PlanValidDays": self.valid_days,
            "PlanTimeExitDays": self.time_exit_days,
            "PlanStopType": self.stop_type,
            "PlanExitRule": self.exit_rule,
            "PlanReason": self.reason,
        }


def _round_price(value: float) -> float:
    return round(max(float(value), 0.01), 2)


def _effective_atr(price: float, atr: float) -> float:
    if price <= 0:
        return max(float(atr), 0.0)
    if atr <= 0 or atr / price < Config.ATR_PCT_FLOOR:
        return price * Config.ATR_PCT_FLOOR_REPLACE
    return float(atr)


def build_shadow_trade_plan(
    decision: SignalDecision,
    *,
    price: float,
    previous_high: float,
    day_low: float,
    ma20: float,
    ma60: float,
    atr: float,
) -> TradePlan:
    """Build a leg-aware V1.3 plan without changing production selection.

    The plan is deliberately simple: an initial intraday stop and a D+40 time
    exit. Moving-average exits, targets, and trailing stops are intentionally
    deferred until forward MFE/MAE evidence exists.
    """
    common = {
        "version": Config.TRADE_PLAN_VERSION,
        "measurement_version": Config.MEASUREMENT_VERSION,
        "selected_leg": decision.selected_leg,
        "time_exit_days": Config.TRADE_PLAN_TIME_EXIT_DAYS,
        "stop_type": "intraday",
        "exit_rule": "initial_stop_or_d40_close",
    }

    if decision.selected_leg is SignalLeg.NONE:
        status = "vetoed" if decision.veto_reason else "not_applicable"
        return TradePlan(
            status=status,
            order_type=OrderType.NONE,
            anchor=decision.anchor,
            anchor_price=decision.anchor_price or None,
            trigger_price=None,
            entry_low=None,
            entry_high=None,
            stop_loss=None,
            valid_days=0,
            reason=decision.veto_reason,
            **common,
        )

    atr_eff = _effective_atr(price, atr)

    if decision.selected_leg is SignalLeg.CONSOLIDATION_DIP:
        anchor_price = ma60
        entry_low = _round_price(anchor_price)
        entry_high = _round_price(anchor_price + 0.5 * atr_eff)
        if price < anchor_price:
            order_type = OrderType.BUY_STOP_RECLAIM
            trigger = entry_low
            reason = "price_below_ma60_wait_for_reclaim"
        else:
            order_type = OrderType.BUY_LIMIT_ZONE
            trigger = entry_high
            reason = "pullback_limit_inside_ma60_zone"
        stop = _round_price(entry_low - 1.5 * atr_eff)
        valid_days = 5

    elif decision.selected_leg is SignalLeg.OVERSOLD_BOUNCE:
        anchor_price = previous_high
        entry_low = _round_price(previous_high + 0.1 * atr_eff)
        entry_high = _round_price(entry_low + 0.25 * atr_eff)
        order_type = OrderType.BUY_STOP_RECLAIM
        trigger = entry_low
        stop_reference = min(day_low - 0.1 * atr_eff, price - 1.5 * atr_eff)
        stop = _round_price(stop_reference)
        valid_days = 5
        reason = "oversold_requires_break_above_previous_high"

    else:
        anchor_price = ma20 if decision.anchor is PlanAnchor.MA20 else ma60
        entry_low = _round_price(anchor_price)
        entry_high = _round_price(anchor_price + 0.5 * atr_eff)
        order_type = OrderType.BUY_LIMIT_ZONE
        trigger = entry_high
        stop = _round_price(entry_low - 1.5 * atr_eff)
        valid_days = 3
        reason = f"pullback_limit_uses_{decision.anchor.value}_anchor"

    if stop >= entry_low:
        stop = _round_price(entry_low * 0.97)

    return TradePlan(
        status="shadow_ready",
        order_type=order_type,
        anchor=decision.anchor,
        anchor_price=_round_price(anchor_price),
        trigger_price=trigger,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop,
        valid_days=valid_days,
        reason=reason,
        **common,
    )


def strategy_config_hash() -> str:
    """Hash only settings that can alter V1.2 selection or V1.3 TradePlans."""
    payload = {
        "signal_engine_version": Config.SIGNAL_ENGINE_VERSION,
        "trade_plan_version": Config.TRADE_PLAN_VERSION,
        "measurement_version": Config.MEASUREMENT_VERSION,
        "ma_short": Config.MA_SHORT_PERIOD,
        "ma_long": Config.MA_LONG_PERIOD,
        "atr_period": Config.ATR_PERIOD,
        "atr_floor": Config.ATR_PCT_FLOOR,
        "atr_floor_replace": Config.ATR_PCT_FLOOR_REPLACE,
        "dip_vol_dry": Config.DIP_VOL_DRY_RATIO,
        "dip_rsi": Config.DIP_RSI_OVERSOLD,
        "score_consolidation": Config.SCORE_CONSOLIDATION_DIP,
        "score_oversold": Config.SCORE_OVERSOLD_BOUNCE,
        "score_pullback": Config.SCORE_HEALTHY_PULLBACK,
        "theme_boost": Config.THEME_BOOST_SCORE,
        "min_priority": Config.MIN_PRIORITY_FOR_GO,
        "top_n": Config.TOP_N_RECOMMENDED,
        "time_exit_days": Config.TRADE_PLAN_TIME_EXIT_DAYS,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]


def universe_version() -> str:
    encoded = json.dumps(sorted(Config.SCAN_POOL), separators=(",", ":")).encode()
    return f"ndx-{len(Config.SCAN_POOL)}-{hashlib.sha256(encoded).hexdigest()[:12]}"
