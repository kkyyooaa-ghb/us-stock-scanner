import unittest

import pandas as pd

from snapshot_metadata import finalize_theme_metadata


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


if __name__ == "__main__":
    unittest.main()
