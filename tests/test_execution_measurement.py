import unittest

import pandas as pd

from config import Config
from execution_measurement import MeasurementStatus, evaluate_trade_plan
from trade_plan import OrderType, PlanAnchor, SignalLeg, TradePlan


def _plan(
    order_type: OrderType,
    *,
    trigger=100.0,
    entry_low=100.0,
    entry_high=100.0,
    stop=90.0,
    valid_days=3,
    time_exit_days=1,
    earliest_entry_date="2026-01-05",
) -> TradePlan:
    return TradePlan(
        status="shadow_ready",
        version=Config.TRADE_PLAN_VERSION,
        measurement_version=Config.SHADOW_MEASUREMENT_VERSION,
        selected_leg=SignalLeg.CONSOLIDATION_DIP,
        order_type=order_type,
        anchor=PlanAnchor.MA60,
        anchor_price=100.0,
        trigger_price=trigger,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop,
        valid_days=valid_days,
        time_exit_days=time_exit_days,
        stop_type="intraday",
        exit_rule="initial_stop_or_d40_close",
        earliest_entry_date=earliest_entry_date,
    )


def _history(rows, start="2026-01-05") -> pd.DataFrame:
    index = pd.bdate_range(start, periods=len(rows))
    normalized = []
    for row in rows:
        normalized.append({
            "Open": row[0],
            "High": row[1],
            "Low": row[2],
            "Close": row[3],
            "Dividends": row[4] if len(row) > 4 else 0.0,
            "Stock Splits": row[5] if len(row) > 5 else 0.0,
        })
    return pd.DataFrame(normalized, index=index)


class OrderSemanticsTests(unittest.TestCase):
    def test_buy_stop_requires_high_to_reach_trigger(self):
        history = _history([
            (95, 99, 80, 90),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(OrderType.BUY_STOP_RECLAIM, valid_days=1),
        )

        self.assertEqual(MeasurementStatus.UNFILLED, result.status)
        self.assertFalse(result.filled)
        self.assertIsNone(result.r_lower)
        self.assertIsNone(result.r_upper)

    def test_buy_limit_same_bar_stop_is_determinate(self):
        history = _history([
            (110, 112, 85, 95),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(OrderType.BUY_LIMIT_ZONE, valid_days=1),
        )

        self.assertEqual(MeasurementStatus.COMPLETED, result.status)
        self.assertTrue(result.filled)
        self.assertFalse(result.ambiguous)
        self.assertEqual("initial_stop", result.exit_reason)
        self.assertEqual(-1.0, result.r_lower)
        self.assertEqual(-1.0, result.r_upper)

    def test_buy_stop_same_bar_entry_and_stop_returns_r_interval(self):
        history = _history([
            (95, 105, 85, 100),
            (105, 112, 101, 110),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(OrderType.BUY_STOP_RECLAIM),
        )

        self.assertEqual(MeasurementStatus.COMPLETED, result.status)
        self.assertTrue(result.ambiguous)
        self.assertEqual(-1.0, result.r_lower)
        self.assertEqual(1.0, result.r_upper)
        self.assertEqual("ambiguous_entry_stop_sequence", result.exit_reason)
        self.assertEqual("2026-01-06", result.lifecycle_end_date)

    def test_future_gap_through_stop_uses_open_price(self):
        history = _history([
            (105, 110, 95, 102),
            (85, 88, 80, 82),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(OrderType.BUY_LIMIT_ZONE),
        )

        self.assertEqual(100.0, result.fill_price)
        self.assertEqual(85.0, result.exit_price)
        self.assertEqual("gap_through_stop", result.exit_reason)
        self.assertEqual(-1.5, result.r_lower)
        self.assertEqual(-1.5, result.r_upper)


class CorporateActionTests(unittest.TestCase):
    def test_split_and_cash_dividend_preserve_as_traded_economics(self):
        history = _history([
            (100, 102, 99, 100, 0.0, 0.0),
            (51, 55, 49, 54, 0.5, 2.0),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(OrderType.BUY_LIMIT_ZONE),
        )

        self.assertEqual(MeasurementStatus.COMPLETED, result.status)
        self.assertEqual("time_exit", result.exit_reason)
        self.assertEqual(108.0, result.exit_price)
        self.assertEqual(1.0, result.dividends_per_initial_share)
        self.assertEqual(2.0, result.ending_split_factor)
        self.assertEqual(0.9, result.r_lower)
        self.assertEqual(0.9, result.r_upper)
        self.assertEqual("2026-01-06", result.lifecycle_end_date)

    def test_split_on_first_eligible_session_normalizes_to_plan_prices(self):
        history = _history([
            (100, 102, 99, 100, 0.0, 0.0),
            (50, 52, 49, 51, 0.0, 2.0),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(
                OrderType.BUY_LIMIT_ZONE,
                earliest_entry_date="2026-01-06",
                valid_days=1,
                time_exit_days=5,
            ),
        )

        self.assertEqual(MeasurementStatus.OPEN, result.status)
        self.assertEqual("2026-01-06", result.fill_date)
        self.assertEqual(100.0, result.fill_price)
        self.assertEqual(2.0, result.ending_split_factor)


class HorizonAndLifecycleTests(unittest.TestCase):
    def test_future_entry_window_is_awaiting_not_no_data(self):
        result = evaluate_trade_plan(
            "2026-01-09",
            pd.DataFrame(),
            _plan(
                OrderType.BUY_LIMIT_ZONE,
                earliest_entry_date="2026-01-12",
            ),
            measurement_as_of_date="2026-01-11",
        )

        self.assertEqual(MeasurementStatus.AWAITING_FILL, result.status)
        self.assertEqual("entry_window_not_started", result.reason)
        self.assertFalse(result.entry_window_complete)
        self.assertIsNone(result.lifecycle_end_date)

    def test_new_measurement_rejects_missing_entry_date_and_old_version(self):
        history = _history([(100, 105, 95, 102)])
        missing_date = _plan(
            OrderType.BUY_LIMIT_ZONE,
            earliest_entry_date=None,
        )
        old_version = _plan(OrderType.BUY_LIMIT_ZONE)
        object.__setattr__(old_version, "measurement_version", "v1.3.0-shadow")

        missing_result = evaluate_trade_plan(
            "2026-01-05",
            history,
            missing_date,
        )
        old_result = evaluate_trade_plan(
            "2026-01-05",
            history,
            old_version,
        )

        self.assertEqual(MeasurementStatus.INVALID_PLAN, missing_result.status)
        self.assertEqual(
            "missing_or_invalid_earliest_entry_date",
            missing_result.reason,
        )
        self.assertEqual(MeasurementStatus.INVALID_PLAN, old_result.status)
        self.assertIn("measurement_version_mismatch", old_result.reason)

    def test_non_executable_leg_cannot_produce_r(self):
        history = _history([(100, 105, 95, 102)])
        plan = _plan(OrderType.BUY_LIMIT_ZONE)
        object.__setattr__(plan, "selected_leg", SignalLeg.NONE)
        object.__setattr__(plan, "anchor", PlanAnchor.NONE)

        result = evaluate_trade_plan("2026-01-05", history, plan)

        self.assertEqual(MeasurementStatus.INVALID_PLAN, result.status)
        self.assertEqual("plan_not_evaluable", result.reason)

    def test_entry_and_horizons_start_on_immutable_earliest_entry_date(self):
        history = _history([
            (95, 105, 85, 100),
            (95, 105, 95, 103),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(
                OrderType.BUY_STOP_RECLAIM,
                valid_days=1,
                time_exit_days=5,
                earliest_entry_date="2026-01-06",
            ),
        )

        self.assertEqual("2026-01-06", result.fill_date)
        self.assertEqual(MeasurementStatus.OPEN, result.status)

        horizon_rows = [(100, 101, 99, 100)]
        for offset in range(21):
            close = 200 + offset
            horizon_rows.append((close, close + 1, close - 1, close))
        horizon_result = evaluate_trade_plan(
            "2026-01-05",
            _history(horizon_rows),
            _plan(
                OrderType.BUY_STOP_RECLAIM,
                trigger=1000,
                entry_low=1000,
                entry_high=1000,
                stop=900,
                valid_days=1,
                earliest_entry_date="2026-01-06",
            ),
        )
        self.assertEqual(10.0, horizon_result.d20_total_return_pct)

    def test_unfilled_trade_never_gets_an_r_value(self):
        history = _history([
            (110, 115, 105, 112),
            (111, 114, 104, 108),
            (109, 113, 103, 107),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(OrderType.BUY_LIMIT_ZONE, valid_days=3),
        )

        self.assertEqual(MeasurementStatus.UNFILLED, result.status)
        self.assertTrue(result.entry_window_complete)
        self.assertEqual("2026-01-07", result.entry_window_end_date)
        self.assertEqual("2026-01-07", result.lifecycle_end_date)
        self.assertIsNone(result.r_lower)
        self.assertIsNone(result.r_upper)

    def test_open_trade_stays_unfinalized_before_time_exit(self):
        history = _history([
            (100, 103, 98, 101),
            (102, 106, 99, 105),
        ])
        result = evaluate_trade_plan(
            "2026-01-05",
            history,
            _plan(OrderType.BUY_LIMIT_ZONE, time_exit_days=5),
        )

        self.assertEqual(MeasurementStatus.OPEN, result.status)
        self.assertIsNone(result.lifecycle_end_date)
        self.assertIsNone(result.r_lower)
        self.assertIsNone(result.r_upper)
        self.assertEqual(0.5, result.mark_r)

    def test_d20_d40_d60_are_total_returns_from_entry_eligible_close(self):
        rows = []
        for day in range(61):
            close = 100.0 + day
            rows.append((close, close + 1, close - 1, close))
        history = _history(rows)
        plan = _plan(
            OrderType.BUY_STOP_RECLAIM,
            trigger=1000,
            entry_low=1000,
            entry_high=1000,
            stop=900,
            valid_days=1,
        )

        result = evaluate_trade_plan("2026-01-05", history, plan)

        self.assertEqual(20.0, result.d20_total_return_pct)
        self.assertEqual(40.0, result.d40_total_return_pct)
        self.assertEqual(60.0, result.d60_total_return_pct)


if __name__ == "__main__":
    unittest.main()
