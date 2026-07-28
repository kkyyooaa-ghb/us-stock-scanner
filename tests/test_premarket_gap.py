"""V1.3.2 盤前跳空契約:分母必須是經日期驗證的訊號棒收盤。

背景(2026-07-28 實測):Yahoo 在盤前時段的 regularMarketPreviousClose 等於
Close[-2] 而非 Close[-1],用它當分母等於把前一個交易日的漲跌幅整個灌進
「跳空」—— 記錄值與該日移動的相關係數達 0.955,93 檔裡有 26 檔被誤標為
「極端跳空 ≥5%」。本檔把這個定義釘死。
"""
import unittest
from datetime import datetime
from unittest import mock

import pandas as pd

import sources
from analyzers import analyze_market_open
from config import Config
from snapshot_metadata import demote_stale_bar_plans


PREMARKET_NOW = datetime(2026, 7, 28, 8, 45, tzinfo=sources.ET_TZ)
SIGNAL_BAR = "2026-07-27"


def _info(pre_price, quote_time_et=None, prev_close=None):
    """Yahoo info 的最小形狀;prev_close 刻意設成會誤導的 Close[-2]。"""
    payload = {"preMarketPrice": pre_price}
    if prev_close is not None:
        payload["regularMarketPreviousClose"] = prev_close
    if quote_time_et is not None:
        payload["preMarketTime"] = quote_time_et.timestamp()
    return payload


def _quote(info, **kwargs):
    params = {
        "reference_close": 100.0,
        "reference_date": SIGNAL_BAR,
        "expected_bar_date": SIGNAL_BAR,
        "now_et": PREMARKET_NOW,
    }
    params.update(kwargs)
    with mock.patch.object(sources, "HAS_YF", True), \
         mock.patch.object(sources, "yf", create=True) as fake_yf:
        fake_yf.Ticker.return_value.info = info
        return sources.get_premarket_quote("TEST", **params)


class PremarketGapDenominatorTests(unittest.TestCase):
    def test_gap_uses_signal_bar_close_not_yahoo_previous_close(self):
        # 訊號棒 100;Yahoo 的 prev_close 是誤導的 90(Close[-2])
        result = _quote(
            _info(102.0, quote_time_et=PREMARKET_NOW, prev_close=90.0)
        )
        self.assertEqual("available", result["status"])
        self.assertEqual(2.0, result["gap_pct"])       # 用 100 → +2%
        self.assertNotEqual(13.33, result["gap_pct"])  # 用 90 會變 +13.3%
        self.assertEqual(100.0, result["reference_price"])
        self.assertEqual(SIGNAL_BAR, result["reference_date"])
        self.assertEqual(
            Config.PREGAP_DEFINITION_VERSION, result["definition_version"]
        )

    def test_stale_signal_bar_produces_no_gap(self):
        result = _quote(
            _info(102.0, quote_time_et=PREMARKET_NOW),
            reference_date="2026-07-24",
        )
        self.assertEqual("stale_reference", result["status"])
        self.assertIsNone(result["gap_pct"])

    def test_stale_premarket_quote_time_produces_no_gap(self):
        yesterday = datetime(2026, 7, 27, 8, 45, tzinfo=sources.ET_TZ)
        result = _quote(_info(102.0, quote_time_et=yesterday))
        self.assertEqual("stale_quote", result["status"])
        self.assertIsNone(result["gap_pct"])

    def test_missing_quote_time_is_not_trusted(self):
        result = _quote(_info(102.0))
        self.assertEqual("stale_quote", result["status"])
        self.assertIsNone(result["gap_pct"])

    def test_no_premarket_trade_is_legal_and_not_an_error(self):
        result = _quote(_info(None, quote_time_et=PREMARKET_NOW))
        self.assertEqual("no_premarket_trade", result["status"])
        self.assertIsNone(result["gap_pct"])

    def test_outside_premarket_window_is_refused(self):
        after_open = datetime(2026, 7, 28, 10, 0, tzinfo=sources.ET_TZ)
        result = _quote(
            _info(102.0, quote_time_et=PREMARKET_NOW), now_et=after_open
        )
        self.assertEqual("outside_premarket_window", result["status"])
        self.assertIsNone(result["gap_pct"])


class MarketLightFailClosedTests(unittest.TestCase):
    def test_invalid_spy_reference_yields_unknown_and_no_entry_advice(self):
        for payload in (
            {"ok": False, "status": "stale_reference", "gap_pct": None},
            {"ok": True, "status": "available", "gap_pct": None},
            {},
        ):
            with self.subTest(payload=payload):
                out = analyze_market_open(payload)
                self.assertEqual("unknown", out["scenario"])
                self.assertEqual("", out["advice"])
                self.assertIsNone(out["gap_pct"])

    def test_valid_reference_still_classifies_normally(self):
        out = analyze_market_open({"ok": True, "gap_pct": -0.1})
        self.assertEqual("normal", out["scenario"])


class StaleBarPlanTests(unittest.TestCase):
    def _frame(self, bar_dates, status="shadow_ready"):
        return pd.DataFrame([
            {
                "Ticker": f"T{i}",
                "DataBarDate": bar_date,
                "TradePlanStatus": status,
                "PlanReason": "pullback_limit_uses_ma20_anchor",
                "Priority": 7,
                "SelectedLeg": "healthy_pullback",
            }
            for i, bar_date in enumerate(bar_dates)
        ])

    def test_stale_bar_plan_is_demoted_and_cannot_reach_shadow_r(self):
        frame = self._frame([SIGNAL_BAR, "2026-07-24"])
        out, n = demote_stale_bar_plans(frame, expected_bar_date=SIGNAL_BAR)
        self.assertEqual(1, n)
        self.assertEqual(
            ["shadow_ready", "data_stale"], list(out["TradePlanStatus"])
        )
        self.assertIn(
            "signal_bar_not_last_complete_session", out["PlanReason"].iloc[1]
        )

    def test_demotion_never_touches_scores_or_legs(self):
        frame = self._frame([SIGNAL_BAR, "2026-07-24"])
        out, _ = demote_stale_bar_plans(frame, expected_bar_date=SIGNAL_BAR)
        for column in ("Ticker", "Priority", "SelectedLeg"):
            self.assertEqual(list(frame[column]), list(out[column]))

    def test_unknown_expected_date_demotes_every_plan(self):
        frame = self._frame([SIGNAL_BAR, SIGNAL_BAR])
        out, n = demote_stale_bar_plans(frame, expected_bar_date=None)
        self.assertEqual(2, n)
        self.assertEqual({"data_stale"}, set(out["TradePlanStatus"]))

    def test_rows_without_a_plan_are_left_alone(self):
        frame = self._frame(["2026-07-24"], status="not_applicable")
        out, n = demote_stale_bar_plans(frame, expected_bar_date=SIGNAL_BAR)
        self.assertEqual(0, n)
        self.assertEqual(["not_applicable"], list(out["TradePlanStatus"]))


if __name__ == "__main__":
    unittest.main()
