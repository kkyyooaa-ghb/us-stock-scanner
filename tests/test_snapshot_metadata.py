import unittest

import pandas as pd

from snapshot_metadata import (
    finalize_snapshot_timing,
    finalize_theme_metadata,
    latest_complete_bar_levels,
)


class ThemeCounterfactualTests(unittest.TestCase):
    def test_marks_threshold_and_top_n_changes_caused_by_theme_score(self):
        frame = pd.DataFrame(
            [
                {
                    "Ticker": "NATIVE_A",
                    "Priority": 8,
                    "Score": 8.5,
                    "PriorityPreTheme": 8,
                    "ScorePreTheme": 8.5,
                },
                {
                    "Ticker": "THEME_PUSH",
                    "Priority": 9,
                    "Score": 9.4,
                    "PriorityPreTheme": 6,
                    "ScorePreTheme": 6.4,
                },
                {
                    "Ticker": "NATIVE_B",
                    "Priority": 7,
                    "Score": 7.8,
                    "PriorityPreTheme": 7,
                    "ScorePreTheme": 7.8,
                },
                {
                    "Ticker": "WATCH",
                    "Priority": 3,
                    "Score": 3.5,
                    "PriorityPreTheme": 3,
                    "ScorePreTheme": 3.5,
                },
            ],
            index=[10, 11, 12, 13],
        )

        result = finalize_theme_metadata(frame, min_priority=7, top_n=2)

        pushed = result.loc[11]
        self.assertEqual(1, pushed["CrossedThresholdDueToTheme"])
        self.assertEqual(1, pushed["EnteredTop10DueToTheme"])
        self.assertEqual(1, pushed["EligiblePostTheme"])

        native = result.loc[10]
        self.assertEqual(0, native["CrossedThresholdDueToTheme"])
        self.assertEqual(0, native["EnteredTop10DueToTheme"])

    def test_empty_frame_is_supported(self):
        result = finalize_theme_metadata(
            pd.DataFrame(),
            min_priority=7,
            top_n=10,
        )
        self.assertTrue(result.empty)


class SnapshotMetadataTests(unittest.TestCase):
    def test_trade_plan_uses_the_latest_complete_signal_bar(self):
        high = pd.Series([110.0, 95.0])
        low = pd.Series([90.0, 88.0])

        signal_high, signal_low = latest_complete_bar_levels(high, low)

        self.assertEqual(95.0, signal_high)
        self.assertEqual(88.0, signal_low)

    def test_missing_complete_bar_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "signal-bar"):
            latest_complete_bar_levels(pd.Series(dtype=float), pd.Series([1.0]))

    def test_snapshot_crossing_open_rebases_every_plan_to_next_session(self):
        frame = pd.DataFrame([
            {
                "Ticker": "READY",
                "SelectedLeg": "oversold_bounce",
                "TradePlanStatus": "shadow_ready",
                "PlanEarliestEntryDate": "2026-07-27",
                "PlanReason": "oversold_requires_break",
            },
            {
                "Ticker": "VETO",
                "SelectedLeg": "none",
                "TradePlanStatus": "vetoed",
                "PlanEarliestEntryDate": "2026-07-27",
                "PlanReason": "negative_yoy",
            },
        ])
        timing = {
            "ok": True,
            "earliest_entry_date": "2026-07-28",
            "scan_session": "after_open",
            "scan_after_open": True,
            "source": "xnys",
        }

        result = finalize_snapshot_timing(
            frame,
            timing=timing,
            snapshot_as_of_et="2026-07-27T09:31:00-04:00",
        )

        self.assertEqual(
            ["2026-07-28", "2026-07-28"],
            result["PlanEarliestEntryDate"].tolist(),
        )
        self.assertTrue(result["ScanAfterOpen"].eq(1).all())
        self.assertTrue(result["ScanSession"].eq("after_open").all())
        self.assertEqual("shadow_ready", result.iloc[0]["TradePlanStatus"])

    def test_final_calendar_failure_disables_selected_plan(self):
        frame = pd.DataFrame([{
            "Ticker": "READY",
            "SelectedLeg": "oversold_bounce",
            "TradePlanStatus": "shadow_ready",
            "PlanEarliestEntryDate": "2026-07-27",
            "PlanReason": "oversold_requires_break",
        }])
        timing = {
            "ok": False,
            "earliest_entry_date": None,
            "scan_session": "calendar_error",
            "scan_after_open": False,
            "source": "unavailable",
        }

        result = finalize_snapshot_timing(
            frame,
            timing=timing,
            snapshot_as_of_et="2026-07-27T09:00:00-04:00",
        )

        self.assertEqual("timing_unavailable", result.iloc[0]["TradePlanStatus"])
        self.assertTrue(pd.isna(result.iloc[0]["PlanEarliestEntryDate"]))
        self.assertIn(
            "entry_calendar_unavailable",
            result.iloc[0]["PlanReason"],
        )


if __name__ == "__main__":
    unittest.main()
