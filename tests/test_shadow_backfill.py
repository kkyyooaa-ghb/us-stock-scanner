import sys
import types
import unittest
from unittest.mock import Mock, patch

import pandas as pd

# The pure snapshot adapter imports the production source module for ET time.
# No HTTP call is made in these tests.
sys.modules.setdefault("requests", types.SimpleNamespace())

import track_shadow_performance
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

    def test_snapshot_plan_is_evaluated_without_mutating_legacy_r(self):
        plan = TradePlan(
            status="shadow_ready",
            version="v1.3.0-shadow",
            measurement_version="v1.3.0-shadow",
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
        self.assertNotIn("R值", result.columns)

    def test_duplicate_snapshot_identity_is_evaluated_once(self):
        plan = TradePlan(
            status="shadow_ready",
            version="v1.3.0-shadow",
            measurement_version="v1.3.0-shadow",
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


if __name__ == "__main__":
    unittest.main()
