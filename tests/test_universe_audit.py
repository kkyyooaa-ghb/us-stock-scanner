"""V1.2.1／v1.3.3 母體契約:每檔恰好一筆紀錄,且對帳必須平衡。

背景(2026-07-28 實測):舊的股數門檻 `MIN_AVG_VOLUME_LOTS = 1000`(100 萬股
/日)沿用台股「張」概念,與真實流動性反向 —— 排除 MPWR(11.45 億美元/日,
高於全池中位數 7.5 億)等 6 檔高價股,卻留下全池唯一低於 1 億的 FER。
被排除的 6 檔完全不進快照,是靜默的、有系統偏誤的資料缺口。

本檔把新契約釘死:可評分者為 data,其餘為 universe_audit 並附原因碼,
expected = processed + excluded + missing 由 schema 永久驗證。
"""
import unittest

import pandas as pd

from config import Config
from snapshot_schema import (
    UNIVERSE_EXCLUSION_REASONS,
    canonicalize_snapshot,
    snapshot_data_rows,
)
from universe_eligibility import complete_session_dollar_volume


def _data_row(ticker="AAA", expected=2, **overrides):
    row = {
        "SnapshotRecordType": "data",
        "UniverseExpectedCount": expected,
        "UniverseDisposition": "processed",
        "UniverseExclusionReason": "",
        "DollarVolumeMedian20": 750_000_000.0,
        "Ticker": ticker,
        "Priority": 7,
        "Score": 7.5,
        "SnapshotAsOfET": "2026-07-28T09:00:00-04:00",
        "DataBarDate": "2026-07-27",
        "ScanSession": "premarket",
        "ScanAfterOpen": 0,
        "SnapshotTimingSource": "xnys",
        "PreGapStatus": "disabled",
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "MeasurementVersion": Config.MEASUREMENT_VERSION,
        "SelectedLeg": "none",
        "LegAnchor": "none",
        "TradePlanStatus": "not_applicable",
        "TradePlanVersion": Config.TRADE_PLAN_VERSION,
        "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "PlanSelectedLeg": "none",
        "OrderType": "none",
        "PlanAnchor": "none",
        "PlanAnchorPrice": pd.NA,
        "PlanEarliestEntryDate": "2026-07-28",
    }
    row.update(overrides)
    return row


def _audit_row(ticker="BBB", reason="liquidity_below_min", expected=2, **overrides):
    row = {
        "SnapshotRecordType": "universe_audit",
        "UniverseExpectedCount": expected,
        "UniverseDisposition": "missing" if reason == "download_missing" else "excluded",
        "UniverseExclusionReason": reason,
        "DollarVolumeMedian20": 5_000_000.0,
        "Ticker": ticker,
        "SnapshotAsOfET": "2026-07-28T09:00:00-04:00",
        "ScanSession": "premarket",
        "ScanAfterOpen": 0,
        "SnapshotTimingSource": "xnys",
        "PreGapStatus": "not_attempted",
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "MeasurementVersion": Config.MEASUREMENT_VERSION,
        "TradePlanStatus": "not_applicable",
        "TradePlanVersion": Config.TRADE_PLAN_VERSION,
        "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
    }
    row.update(overrides)
    return row


class UniverseReconciliationTests(unittest.TestCase):
    def test_balanced_universe_is_accepted(self):
        frame = pd.DataFrame([_data_row(), _audit_row()])

        result = canonicalize_snapshot(frame)

        self.assertEqual(
            ["data", "universe_audit"], list(result["SnapshotRecordType"])
        )
        self.assertEqual([2, 2], list(result["UniverseExpectedCount"]))

    def test_short_universe_is_rejected(self):
        # expected 說 3 檔,實際只留下 2 筆紀錄 → 有股票被靜默吞掉
        frame = pd.DataFrame([
            _data_row(expected=3), _audit_row(expected=3),
        ])

        with self.assertRaises(ValueError) as ctx:
            canonicalize_snapshot(frame)
        self.assertIn("universe reconciliation failed", str(ctx.exception))

    def test_duplicate_ticker_is_rejected(self):
        frame = pd.DataFrame([_data_row("AAA"), _audit_row("AAA")])

        with self.assertRaises(ValueError) as ctx:
            canonicalize_snapshot(frame)
        self.assertIn("recorded more than once", str(ctx.exception))

    def test_audit_row_may_not_carry_a_score(self):
        """零分偽裝正是要禁止的:下游會把它當成一檔評分很差的普通股票。"""
        for column in ("Priority", "Score"):
            with self.subTest(column=column):
                frame = pd.DataFrame([
                    _data_row(), _audit_row(**{column: 0}),
                ])
                with self.assertRaises(ValueError) as ctx:
                    canonicalize_snapshot(frame)
                self.assertIn(f"must leave {column} empty", str(ctx.exception))

    def test_audit_row_requires_a_known_reason(self):
        frame = pd.DataFrame([_data_row(), _audit_row(
            UniverseExclusionReason="because_i_said_so"
        )])

        with self.assertRaises(ValueError) as ctx:
            canonicalize_snapshot(frame)
        self.assertIn("invalid UniverseExclusionReason", str(ctx.exception))

    def test_every_documented_reason_code_is_accepted(self):
        self.assertEqual(6, len(UNIVERSE_EXCLUSION_REASONS))
        for reason in UNIVERSE_EXCLUSION_REASONS:
            with self.subTest(reason=reason):
                frame = pd.DataFrame([
                    _data_row(), _audit_row(reason=reason),
                ])
                self.assertEqual(2, len(canonicalize_snapshot(frame)))

    def test_download_failure_must_be_counted_as_missing(self):
        frame = pd.DataFrame([
            _data_row(),
            _audit_row(
                reason="download_missing",
                UniverseDisposition="excluded",
            ),
        ])

        with self.assertRaisesRegex(
            ValueError,
            "does not match UniverseExclusionReason",
        ):
            canonicalize_snapshot(frame)

    def test_non_download_failure_must_be_counted_as_excluded(self):
        frame = pd.DataFrame([
            _data_row(),
            _audit_row(
                reason="stale_bar",
                UniverseDisposition="missing",
            ),
        ])

        with self.assertRaisesRegex(
            ValueError,
            "does not match UniverseExclusionReason",
        ):
            canonicalize_snapshot(frame)

    def test_expected_count_must_be_an_integer(self):
        frame = pd.DataFrame([
            _data_row(expected=2.5),
            _audit_row(expected=2.5),
        ])

        with self.assertRaisesRegex(ValueError, "finite integer"):
            canonicalize_snapshot(frame)

    def test_data_row_cannot_claim_to_be_excluded(self):
        frame = pd.DataFrame([
            _data_row(UniverseDisposition="excluded"), _audit_row(),
        ])

        with self.assertRaises(ValueError) as ctx:
            canonicalize_snapshot(frame)
        self.assertIn("data rows require processed", str(ctx.exception))

    def test_audit_rows_never_reach_downstream_consumers(self):
        """週報彙總與 shadow 量尺都經由 snapshot_data_rows 取列。"""
        frame = canonicalize_snapshot(
            pd.DataFrame([_data_row("AAA"), _audit_row("BBB")])
        )

        data = snapshot_data_rows(frame)

        self.assertEqual(["AAA"], list(data["Ticker"]))


class LiquidityRuleTests(unittest.TestCase):
    def test_dollar_threshold_replaces_share_count_rule(self):
        self.assertFalse(hasattr(Config, "MIN_AVG_VOLUME_LOTS"))
        self.assertEqual(20_000_000, Config.MIN_DOLLAR_VOLUME_USD)
        self.assertEqual(20, Config.LIQUIDITY_LOOKBACK_DAYS)
        self.assertEqual("median", Config.LIQUIDITY_STATISTIC)

    def test_eligibility_rules_are_part_of_the_cohort_identity(self):
        """母體資格會改變主題觸發與 Top 10,必須讓 ConfigHash 認得。"""
        from trade_plan import strategy_config_hash

        baseline = strategy_config_hash()
        original = Config.MIN_DOLLAR_VOLUME_USD
        try:
            Config.MIN_DOLLAR_VOLUME_USD = original * 2
            self.assertNotEqual(baseline, strategy_config_hash())
        finally:
            Config.MIN_DOLLAR_VOLUME_USD = original
        self.assertEqual(baseline, strategy_config_hash())

    def test_median_is_robust_to_a_single_volume_spike(self):
        """選中位數而非均值的理由:財報日單日暴量不該讓一檔通過門檻。"""
        close = pd.Series([10.0] * 20)
        volume = pd.Series([100_000] * 19 + [40_000_000])
        result = complete_session_dollar_volume(
            close,
            volume,
            lookback_days=Config.LIQUIDITY_LOOKBACK_DAYS,
            statistic=Config.LIQUIDITY_STATISTIC,
        )

        self.assertLess(result, Config.MIN_DOLLAR_VOLUME_USD)
        self.assertGreater(float((close * volume).mean()),
                           Config.MIN_DOLLAR_VOLUME_USD)

    def test_partial_volume_window_is_not_eligible(self):
        dates = pd.date_range("2026-06-01", periods=20, freq="B")
        close = pd.Series([100.0] * 20, index=dates)
        volume = pd.Series([1_000_000.0] * 5, index=dates[-5:])

        result = complete_session_dollar_volume(
            close,
            volume,
            lookback_days=Config.LIQUIDITY_LOOKBACK_DAYS,
            statistic=Config.LIQUIDITY_STATISTIC,
        )

        self.assertIsNone(result)

    def test_misaligned_volume_window_is_not_eligible(self):
        dates = pd.date_range("2026-06-01", periods=21, freq="B")
        close = pd.Series([100.0] * 20, index=dates[-20:])
        volume = pd.Series([1_000_000.0] * 20, index=dates[:-1])

        result = complete_session_dollar_volume(
            close,
            volume,
            lookback_days=Config.LIQUIDITY_LOOKBACK_DAYS,
            statistic=Config.LIQUIDITY_STATISTIC,
        )

        self.assertIsNone(result)

    def test_data_row_requires_finite_eligible_dollar_volume(self):
        for value in (float("nan"), 19_999_999.0):
            with self.subTest(value=value):
                frame = pd.DataFrame([
                    _data_row(DollarVolumeMedian20=value),
                    _audit_row(),
                ])
                with self.assertRaisesRegex(
                    ValueError,
                    "DollarVolumeMedian20",
                ):
                    canonicalize_snapshot(frame)


if __name__ == "__main__":
    unittest.main()
