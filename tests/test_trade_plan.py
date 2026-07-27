import unittest
import sys
import types

# Codex bundled test runtime intentionally omits network clients. These tests
# cross only the pure signal/TradePlan interface and never perform HTTP calls.
sys.modules.setdefault("requests", types.SimpleNamespace())

from analyzers import determine_status, determine_status_details
from trade_plan import (
    OrderType,
    PlanAnchor,
    SignalLeg,
    build_shadow_trade_plan,
    strategy_config_hash,
    universe_version,
)


EMPTY_BROKER = {
    "key_broker_hits": [],
    "max_consec_days": 0,
    "day_trader_warn": False,
}


class SignalDecisionTests(unittest.TestCase):
    def test_legacy_tuple_interface_is_preserved(self):
        result = determine_status(
            "consolidate",
            EMPTY_BROKER,
            0.5,
            False,
            dist_tag="🎯 甜點價",
            price=100,
            prev_close=99,
            day_low=98,
            ma20=101,
            ma60=100,
            yoy=0.2,
        )
        self.assertEqual(("📐 盤整甜點位｜量縮低接", 10), result)

    def test_consolidation_leg_is_structured(self):
        decision = determine_status_details(
            "consolidate",
            EMPTY_BROKER,
            0.5,
            False,
            dist_tag="🎯 甜點價",
            price=98,
            prev_close=99,
            day_low=97,
            ma20=101,
            ma60=100,
            yoy=0.2,
        )
        self.assertEqual(SignalLeg.CONSOLIDATION_DIP, decision.candidate_leg)
        self.assertEqual(SignalLeg.CONSOLIDATION_DIP, decision.selected_leg)
        self.assertEqual(10, decision.leg_score_raw)
        self.assertEqual(PlanAnchor.MA60, decision.anchor)

    def test_negative_yoy_preserves_candidate_but_vetoes_selection(self):
        decision = determine_status_details(
            "consolidate",
            EMPTY_BROKER,
            0.5,
            False,
            dist_tag="🎯 甜點價",
            price=100,
            prev_close=99,
            day_low=98,
            ma20=101,
            ma60=100,
            yoy=-0.1,
        )
        self.assertEqual(SignalLeg.CONSOLIDATION_DIP, decision.candidate_leg)
        self.assertEqual(SignalLeg.NONE, decision.selected_leg)
        self.assertEqual("negative_revenue_yoy", decision.veto_reason)
        self.assertEqual(0, decision.base_priority)


class TradePlanTests(unittest.TestCase):
    def test_price_below_ma60_requires_reclaim_for_consolidation(self):
        decision = determine_status_details(
            "consolidate",
            EMPTY_BROKER,
            0.5,
            False,
            dist_tag="🎯 甜點價",
            price=98,
            prev_close=99,
            day_low=97,
            ma20=101,
            ma60=100,
            yoy=0.2,
        )
        plan = build_shadow_trade_plan(
            decision,
            price=98,
            previous_high=99,
            day_low=97,
            ma20=101,
            ma60=100,
            atr=3,
        )
        self.assertEqual("shadow_ready", plan.status)
        self.assertEqual(OrderType.BUY_STOP_RECLAIM, plan.order_type)
        self.assertEqual(100, plan.trigger_price)
        self.assertLess(plan.stop_loss, plan.entry_low)

    def test_oversold_leg_now_has_a_measurable_reclaim_plan(self):
        decision = determine_status_details(
            "transition_low",
            EMPTY_BROKER,
            0.9,
            False,
            dist_tag="⚠️ 已偏離",
            rsi=30,
            price=80,
            prev_close=79,
            day_low=77,
            ma20=90,
            ma60=100,
            yoy=0.1,
        )
        self.assertEqual(SignalLeg.OVERSOLD_BOUNCE, decision.selected_leg)

        plan = build_shadow_trade_plan(
            decision,
            price=80,
            previous_high=82,
            day_low=77,
            ma20=90,
            ma60=100,
            atr=4,
        )
        self.assertEqual(OrderType.BUY_STOP_RECLAIM, plan.order_type)
        self.assertGreater(plan.entry_low, 0)
        self.assertGreaterEqual(plan.trigger_price, 82)
        self.assertLess(plan.stop_loss, plan.entry_low)

    def test_healthy_ma20_pullback_plan_uses_ma20_not_ma60(self):
        decision = determine_status_details(
            "consolidate",
            EMPTY_BROKER,
            0.9,
            False,
            dist_tag="📍 偏離待回 ↑",
            rsi=50,
            price=102,
            prev_close=100,
            day_low=100,
            ma20=100,
            ma60=90,
            yoy=0.1,
        )
        self.assertEqual(SignalLeg.HEALTHY_PULLBACK, decision.selected_leg)
        self.assertEqual(PlanAnchor.MA20, decision.anchor)

        plan = build_shadow_trade_plan(
            decision,
            price=102,
            previous_high=103,
            day_low=100,
            ma20=100,
            ma60=90,
            atr=4,
        )
        self.assertEqual(OrderType.BUY_LIMIT_ZONE, plan.order_type)
        self.assertEqual(100, plan.anchor_price)
        self.assertEqual(100, plan.entry_low)
        self.assertNotEqual(90, plan.entry_low)

    def test_vetoed_signal_has_no_trade_plan_prices(self):
        decision = determine_status_details(
            "consolidate",
            EMPTY_BROKER,
            0.5,
            False,
            dist_tag="🎯 甜點價",
            price=100,
            prev_close=99,
            day_low=98,
            ma20=101,
            ma60=100,
            yoy=-0.1,
        )
        plan = build_shadow_trade_plan(
            decision,
            price=100,
            previous_high=102,
            day_low=98,
            ma20=101,
            ma60=100,
            atr=3,
        )
        self.assertEqual("vetoed", plan.status)
        self.assertEqual(OrderType.NONE, plan.order_type)
        self.assertIsNone(plan.trigger_price)
        self.assertIsNone(plan.stop_loss)

    def test_versions_are_deterministic(self):
        self.assertEqual(strategy_config_hash(), strategy_config_hash())
        self.assertRegex(universe_version(), r"^ndx-99-[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
