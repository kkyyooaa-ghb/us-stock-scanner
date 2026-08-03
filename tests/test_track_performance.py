import io
import sys
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import pandas as pd

# Codex 隨附的最小 Python 不含 requests；本測試會 mock 所有 HTTP 邊界。
sys.modules.setdefault("requests", types.SimpleNamespace())

import track_performance


class TrackPerformanceMainTests(unittest.TestCase):
    def test_early_stop_before_d5_is_reported_without_crashing(self):
        dates = pd.to_datetime(["2026-07-31", "2026-08-03"])
        close = pd.Series([100.0, 98.0], index=dates)
        low = pd.Series([99.0, 89.0], index=dates)
        page = {
            "page_id": "page-1",
            "title": "2026-07-31_TEST",
            "ticker": "TEST",
            "scan_date": "2026-07-31",
            "entry": 100.0,
            "stop": 90.0,
            "note_empty": True,
        }
        updates = []

        def fake_get_series(_data, field, _ticker):
            return close if field == "Close" else low

        def fake_update_page(page_id, props):
            updates.append((page_id, props))
            return True

        output = io.StringIO()
        with (
            patch.object(track_performance, "NOTION_TOKEN", "test-token"),
            patch.object(track_performance, "NOTION_DB_ID", "test-db"),
            patch.object(track_performance, "HAS_YF", True),
            patch.object(
                track_performance,
                "fetch_pages_needing_backfill",
                return_value=[page],
            ),
            patch.object(
                track_performance,
                "download_history",
                return_value=pd.DataFrame({"present": [1.0]}, index=dates[:1]),
            ),
            patch.object(
                track_performance,
                "_drop_partial_today",
                side_effect=lambda df: df,
            ),
            patch.object(track_performance, "get_series", side_effect=fake_get_series),
            patch.object(track_performance, "update_page", side_effect=fake_update_page),
            patch.object(track_performance.time, "sleep"),
            redirect_stdout(output),
        ):
            exit_code = track_performance.main()

        self.assertEqual(0, exit_code)
        self.assertIn("D+5 未到", output.getvalue())
        self.assertIn("R-1.00", output.getvalue())
        self.assertIn("🛑停損", output.getvalue())
        self.assertEqual(1, len(updates))
        self.assertEqual("page-1", updates[0][0])
        props = updates[0][1]
        self.assertNotIn("D+5報酬%", props)
        self.assertEqual(-1.0, props["R值"]["number"])
        self.assertTrue(props["是否觸發停損"]["checkbox"])


if __name__ == "__main__":
    unittest.main()
