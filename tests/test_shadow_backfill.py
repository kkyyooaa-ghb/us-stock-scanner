import sys
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

# The pure snapshot adapter imports the production source module for ET time.
# No HTTP call is made in these tests.
sys.modules.setdefault("requests", types.SimpleNamespace())

import track_shadow_performance
from config import Config
from track_shadow_performance import (
    download_as_traded_histories,
    evaluate_snapshot,
)
from trade_plan import OrderType, PlanAnchor, SignalLeg, TradePlan


class SnapshotAdapterTests(unittest.TestCase):
    def test_download_adapter_requests_raw_ohlc_and_actions(self):
        columns = pd.MultiIndex.from_tuples([
            ("Open", "TEST"),
            ("High", "TEST"),
            ("Low", "TEST"),
            ("Close", "TEST"),
            ("Dividends", "TEST"),
            ("Stock Splits", "TEST"),
        ])
        downloaded = pd.DataFrame(
            [[100, 105, 99, 103, 0.5, 2.0]],
            index=pd.to_datetime(["2026-01-05"]),
            columns=columns,
        )
        fake_yf = Mock()
        fake_yf.download.return_value = downloaded

        with patch.object(track_shadow_performance, "yf", fake_yf, create=True):
            histories = download_as_traded_histories(["TEST"], "2026-01-05")

        kwargs = fake_yf.download.call_args.kwargs
        self.assertFalse(kwargs["auto_adjust"])
        self.assertTrue(kwargs["actions"])
        self.assertFalse(kwargs["repair"])
        self.assertEqual(0.5, histories["TEST"].iloc[0]["Dividends"])
        self.assertEqual(2.0, histories["TEST"].iloc[0]["Stock Splits"])

    def test_history_download_freezes_bar_cutoff_before_network_call(self):
        downloaded = pd.DataFrame(
            {
                "Open": [100],
                "High": [105],
                "Low": [99],
                "Close": [103],
                "Dividends": [0],
                "Stock Splits": [0],
            },
            index=pd.to_datetime(["2026-07-27"]),
        )
        fake_yf = Mock()
        fake_yf.download.return_value = downloaded
        frozen_timing = {
            "current_daily_bar_complete": False,
            "last_complete_session_date": "2026-07-24",
        }

        with (
            patch.object(track_shadow_performance, "yf", fake_yf, create=True),
            patch.object(
                track_shadow_performance,
                "resolve_plan_entry_timing",
                return_value=frozen_timing,
            ),
        ):
            histories = download_as_traded_histories(
                ["TEST"],
                "2026-07-27",
                observation_time_et=datetime(
                    2026,
                    7,
                    27,
                    16,
                    14,
                    59,
                    tzinfo=ZoneInfo("America/New_York"),
                ),
            )

        self.assertTrue(histories["TEST"].empty)

    def test_snapshot_plan_is_evaluated_without_mutating_legacy_r(self):
        plan = TradePlan(
            status="shadow_ready",
            version=Config.TRADE_PLAN_VERSION,
            measurement_version=Config.SHADOW_MEASUREMENT_VERSION,
            selected_leg=SignalLeg.CONSOLIDATION_DIP,
            order_type=OrderType.BUY_LIMIT_ZONE,
            anchor=PlanAnchor.MA60,
            anchor_price=100,
            trigger_price=102,
            entry_low=100,
            entry_high=102,
            stop_loss=90,
            valid_days=3,
            time_exit_days=1,
            stop_type="intraday",
            exit_rule="initial_stop_or_d40_close",
            earliest_entry_date="2026-01-05",
        )
        snapshot = pd.DataFrame([{
            "SnapshotAsOfET": "2026-01-05T09:00:00-05:00",
            "DataBarDate": "2026-01-02",
            "Ticker": "TEST",
            "SignalEngineVersion": "v1.2.0",
            "ConfigHash": "abc",
            "UniverseVersion": "test",
            "SelectedLeg": "consolidation_dip",
            "R值": 99.0,
            **plan.to_snapshot_fields(),
        }])
        history = pd.DataFrame(
            [
                {
                    "Open": 105,
                    "High": 108,
                    "Low": 100,
                    "Close": 103,
                    "Dividends": 0,
                    "Stock Splits": 0,
                },
                {
                    "Open": 104,
                    "High": 110,
                    "Low": 101,
                    "Close": 108,
                    "Dividends": 0,
                    "Stock Splits": 0,
                },
            ],
            index=pd.bdate_range("2026-01-05", periods=2),
        )

        result = evaluate_snapshot(snapshot, {"TEST": history})

        self.assertEqual(1, len(result))
        self.assertEqual("completed", result.iloc[0]["V13MeasurementStatus"])
        self.assertEqual(0.5, result.iloc[0]["V13RLower"])
        self.assertEqual(
            "2026-01-05",
            result.iloc[0]["PlanEarliestEntryDate"],
        )
        self.assertEqual(100, result.iloc[0]["PlanAnchorPrice"])
        self.assertEqual(90, result.iloc[0]["PlanStopLoss"])
        self.assertNotIn("R值", result.columns)

    def test_missing_entry_date_is_invalid_not_scan_date_fallback(self):
        plan = TradePlan(
            status="shadow_ready",
            version=Config.TRADE_PLAN_VERSION,
            measurement_version=Config.SHADOW_MEASUREMENT_VERSION,
            selected_leg=SignalLeg.CONSOLIDATION_DIP,
            order_type=OrderType.BUY_LIMIT_ZONE,
            anchor=PlanAnchor.MA60,
            anchor_price=100,
            trigger_price=102,
            entry_low=100,
            entry_high=102,
            stop_loss=90,
            valid_days=3,
            time_exit_days=1,
            stop_type="intraday",
            exit_rule="initial_stop_or_d40_close",
            earliest_entry_date=None,
        )
        snapshot = pd.DataFrame([{
            "SnapshotAsOfET": "2026-01-05T09:00:00-05:00",
            "Ticker": "TEST",
            **plan.to_snapshot_fields(),
        }])

        result = evaluate_snapshot(snapshot, {"TEST": pd.DataFrame()})

        self.assertEqual("invalid_plan", result.iloc[0]["V13MeasurementStatus"])
        self.assertEqual(
            "missing_or_invalid_earliest_entry_date",
            result.iloc[0]["V13MeasurementReason"],
        )

    def test_duplicate_snapshot_identity_is_evaluated_once(self):
        plan = TradePlan(
            status="shadow_ready",
            version=Config.TRADE_PLAN_VERSION,
            measurement_version=Config.SHADOW_MEASUREMENT_VERSION,
            selected_leg=SignalLeg.OVERSOLD_BOUNCE,
            order_type=OrderType.BUY_STOP_RECLAIM,
            anchor=PlanAnchor.PREVIOUS_HIGH,
            anchor_price=100,
            trigger_price=101,
            entry_low=101,
            entry_high=102,
            stop_loss=90,
            valid_days=1,
            time_exit_days=40,
            stop_type="intraday",
            exit_rule="initial_stop_or_d40_close",
            earliest_entry_date="2026-01-05",
        )
        row = {
            "SnapshotAsOfET": "2026-01-05T09:00:00-05:00",
            "Ticker": "TEST",
            **plan.to_snapshot_fields(),
        }
        snapshot = pd.DataFrame([row, row])

        result = evaluate_snapshot(snapshot, {"TEST": pd.DataFrame()})

        self.assertEqual(1, len(result))
        self.assertEqual("no_data", result.iloc[0]["V13MeasurementStatus"])

    def test_snapshot_waits_when_next_session_has_not_started(self):
        plan = TradePlan(
            status="shadow_ready",
            version=Config.TRADE_PLAN_VERSION,
            measurement_version=Config.SHADOW_MEASUREMENT_VERSION,
            selected_leg=SignalLeg.OVERSOLD_BOUNCE,
            order_type=OrderType.BUY_STOP_RECLAIM,
            anchor=PlanAnchor.PREVIOUS_HIGH,
            anchor_price=100,
            trigger_price=101,
            entry_low=101,
            entry_high=102,
            stop_loss=90,
            valid_days=5,
            time_exit_days=40,
            stop_type="intraday",
            exit_rule="initial_stop_or_d40_close",
            earliest_entry_date="2026-01-12",
        )
        snapshot = pd.DataFrame([{
            "SnapshotAsOfET": "2026-01-09T16:00:00-05:00",
            "Ticker": "TEST",
            **plan.to_snapshot_fields(),
        }])

        result = evaluate_snapshot(
            snapshot,
            {"TEST": pd.DataFrame()},
            measurement_as_of_date="2026-01-11",
        )

        self.assertEqual("awaiting_fill", result.iloc[0]["V13MeasurementStatus"])
        self.assertEqual(
            "entry_window_not_started",
            result.iloc[0]["V13MeasurementReason"],
        )

    def test_same_day_premarket_measurement_uses_last_complete_session(self):
        plan = TradePlan(
            status="shadow_ready",
            version=Config.TRADE_PLAN_VERSION,
            measurement_version=Config.SHADOW_MEASUREMENT_VERSION,
            selected_leg=SignalLeg.OVERSOLD_BOUNCE,
            order_type=OrderType.BUY_STOP_RECLAIM,
            anchor=PlanAnchor.PREVIOUS_HIGH,
            anchor_price=100,
            trigger_price=101,
            entry_low=101,
            entry_high=102,
            stop_loss=90,
            valid_days=5,
            time_exit_days=40,
            stop_type="intraday",
            exit_rule="initial_stop_or_d40_close",
            earliest_entry_date="2026-01-12",
        )
        snapshot = pd.DataFrame([{
            "SnapshotAsOfET": "2026-01-12T09:00:00-05:00",
            "Ticker": "TEST",
            **plan.to_snapshot_fields(),
        }])

        result = evaluate_snapshot(
            snapshot,
            {"TEST": pd.DataFrame()},
            measurement_as_of_date="2026-01-09",
        )

        self.assertEqual("awaiting_fill", result.iloc[0]["V13MeasurementStatus"])
        self.assertEqual(
            "entry_window_not_started",
            result.iloc[0]["V13MeasurementReason"],
        )


if __name__ == "__main__":
    unittest.main()
