import sys
import types
import unittest
from datetime import datetime
from importlib.util import find_spec
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

sys.modules.setdefault("requests", types.SimpleNamespace())

import sources
from sources import is_premarket_quote_window, resolve_plan_entry_timing


ET = ZoneInfo("America/New_York")


class _FakeCalendar:
    sessions = {
        "2026-07-27": "2026-07-28",
        "2026-07-28": "2026-07-29",
    }

    def is_session(self, date):
        return str(date)[:10] in self.sessions

    def session_open(self, date):
        return pd.Timestamp(f"{str(date)[:10]} 13:30:00", tz="UTC")

    def session_close(self, date):
        return pd.Timestamp(f"{str(date)[:10]} 20:00:00", tz="UTC")

    def previous_session(self, date):
        return pd.Timestamp("2026-07-24")

    def next_session(self, date):
        return pd.Timestamp(self.sessions[str(date)[:10]])

    def date_to_session(self, date, direction):
        self.directions = getattr(self, "directions", [])
        self.directions.append(direction)
        return pd.Timestamp(
            "2026-07-27" if direction == "next" else "2026-07-24"
        )


class SnapshotTimingTests(unittest.TestCase):
    def setUp(self):
        self.calendar = _FakeCalendar()
        self.calendar_patch = patch.object(
            sources,
            "_get_nyse_calendar",
            return_value=self.calendar,
        )
        self.calendar_patch.start()

    def tearDown(self):
        self.calendar_patch.stop()

    def test_premarket_plan_can_trade_in_the_same_session(self):
        result = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 9, 0, tzinfo=ET)
        )

        self.assertTrue(result["ok"])
        self.assertEqual("2026-07-27", result["earliest_entry_date"])
        self.assertEqual("premarket", result["scan_session"])
        self.assertFalse(result["scan_after_open"])
        self.assertEqual(
            "2026-07-24",
            result["last_complete_session_date"],
        )

    def test_preopen_and_premarket_quote_windows_are_distinct(self):
        before_quotes = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 3, 59, tzinfo=ET)
        )
        quotes_open = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 4, 0, tzinfo=ET)
        )

        self.assertEqual("preopen", before_quotes["scan_session"])
        self.assertEqual("premarket", quotes_open["scan_session"])
        self.assertEqual(
            "2026-07-27",
            before_quotes["earliest_entry_date"],
        )

    def test_quote_window_uses_wall_clock_even_without_calendar(self):
        self.assertFalse(is_premarket_quote_window(
            datetime(2026, 7, 27, 3, 59, tzinfo=ET)
        ))
        self.assertTrue(is_premarket_quote_window(
            datetime(2026, 7, 27, 4, 0, tzinfo=ET)
        ))
        self.assertTrue(is_premarket_quote_window(
            datetime(2026, 7, 27, 9, 29, tzinfo=ET)
        ))
        self.assertFalse(is_premarket_quote_window(
            datetime(2026, 7, 27, 9, 30, tzinfo=ET)
        ))

    def test_market_open_and_later_wait_for_the_next_session(self):
        at_open = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 9, 30, tzinfo=ET)
        )
        after_close = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 17, 0, tzinfo=ET)
        )

        self.assertEqual("2026-07-28", at_open["earliest_entry_date"])
        self.assertEqual("2026-07-28", after_close["earliest_entry_date"])
        self.assertTrue(at_open["scan_after_open"])
        self.assertEqual("after_open", after_close["scan_session"])
        self.assertFalse(at_open["current_session_closed"])
        self.assertTrue(after_close["current_session_closed"])

    def test_daily_bar_waits_for_close_buffer(self):
        at_close = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 16, 0, tzinfo=ET)
        )
        before_buffer = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 16, 14, tzinfo=ET)
        )
        after_buffer = resolve_plan_entry_timing(
            datetime(2026, 7, 27, 16, 15, tzinfo=ET)
        )

        self.assertFalse(at_close["current_daily_bar_complete"])
        self.assertFalse(before_buffer["current_daily_bar_complete"])
        self.assertTrue(after_buffer["current_daily_bar_complete"])
        self.assertEqual(
            "2026-07-24",
            before_buffer["last_complete_session_date"],
        )
        self.assertEqual(
            "2026-07-27",
            after_buffer["last_complete_session_date"],
        )

    def test_non_session_uses_the_next_exchange_session(self):
        result = resolve_plan_entry_timing(
            datetime(2026, 7, 26, 12, 0, tzinfo=ET)
        )

        self.assertEqual("2026-07-27", result["earliest_entry_date"])
        self.assertEqual("non_session", result["scan_session"])
        self.assertEqual(["next", "previous"], self.calendar.directions)

    def test_calendar_failure_is_explicit_and_has_no_guessed_date(self):
        with patch.object(
            sources,
            "_get_nyse_calendar",
            side_effect=RuntimeError("calendar unavailable"),
        ):
            result = resolve_plan_entry_timing(
                datetime(2026, 7, 27, 9, 0, tzinfo=ET)
            )

        self.assertFalse(result["ok"])
        self.assertIsNone(result["earliest_entry_date"])
        self.assertEqual("calendar_error", result["scan_session"])


@unittest.skipUnless(
    find_spec("exchange_calendars") is not None,
    "exchange_calendars is installed in the production environment",
)
class RealExchangeCalendarTests(unittest.TestCase):
    def setUp(self):
        sources._NYSE_CAL = None

    def tearDown(self):
        sources._NYSE_CAL = None

    def test_observed_independence_day_uses_next_real_xnys_session(self):
        result = resolve_plan_entry_timing(
            datetime(2026, 7, 3, 12, 0, tzinfo=ET)
        )

        self.assertTrue(result["ok"])
        self.assertEqual("non_session", result["scan_session"])
        self.assertEqual("2026-07-06", result["earliest_entry_date"])


if __name__ == "__main__":
    unittest.main()
