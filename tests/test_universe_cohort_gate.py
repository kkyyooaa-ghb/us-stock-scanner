"""母體一致性閘門測試。

守的是一個具體破口:`SCAN_POOL` 不在 `strategy_config_hash()` 內,所以增刪
成分股**不會**讓 cohort 歸零,卻會改變主題觸發與 Top 10 —— 同一份調參樣本
因此可能混進兩套選股母體,而在此改動前沒有任何地方會發現。
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from episode_analysis import _universe_cohort, build_episode_analysis
from trade_plan import strategy_config_hash, universe_version
from weekly_report import load_v13_calibration_gate, build_message


def _signal(ticker, date, *, universe=None, status="completed", r=-1.0):
    return {
        "SnapshotAsOfET": f"{date}T09:00:00-05:00",
        "Ticker": ticker,
        "TradePlanVersion": Config.TRADE_PLAN_VERSION,
        "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "V13MeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "ConfigHash": strategy_config_hash(),
        "UniverseVersion": universe or universe_version(),
        "PlanSelectedLeg": "healthy_pullback",
        "SelectedLeg": "healthy_pullback",
        "OrderType": "buy_limit_zone",
        "V13MeasurementStatus": status,
        "V13Filled": 1,
        "V13Ambiguous": 0,
        "V13RLower": r,
        "V13RUpper": r,
        "V13MFER": 1.5,
        "V13MAER": -0.5,
        "V13LifecycleEndDate": date,
        "V13EntryWindowEndDate": None,
    }


def _performance(count, *, split_universe_after=None):
    """造 count 筆已完成訊號;split_universe_after 之後改用另一套母體。"""
    rows = []
    for i in range(count):
        other = (
            split_universe_after is not None and i >= split_universe_after
        )
        rows.append(
            _signal(
                f"TK{i:03d}",
                f"2026-07-{(i % 20) + 1:02d}",
                universe="ndx-98-000000000000" if other else None,
            )
        )
    return pd.DataFrame(rows)


class UniverseCohortDetection(unittest.TestCase):
    def test_single_universe_is_consistent(self):
        result = _universe_cohort(_performance(5))
        self.assertTrue(result["consistent"])
        self.assertEqual(result["distinct"], 1)
        self.assertIsNone(result["reason"])

    def test_mixed_universes_detected(self):
        result = _universe_cohort(_performance(10, split_universe_after=5))
        self.assertFalse(result["consistent"])
        self.assertTrue(result["mixed"])
        self.assertEqual(result["distinct"], 2)
        self.assertEqual(result["reason"], "multiple_universe_versions")

    def test_single_but_stale_universe_detected(self):
        """整批都是舊母體 —— 不是混合,但已非現行母體,同樣不可調參。"""
        frame = _performance(5, split_universe_after=0)
        result = _universe_cohort(frame)
        self.assertFalse(result["consistent"])
        self.assertFalse(result["mixed"])
        self.assertEqual(result["reason"], "universe_changed_since_cohort_start")

    def test_missing_column_fails_closed(self):
        frame = _performance(3).drop(columns=["UniverseVersion"])
        result = _universe_cohort(frame)
        self.assertFalse(result["consistent"])
        self.assertEqual(result["reason"], "universe_version_column_missing")

    def test_empty_frame_is_vacuously_consistent(self):
        result = _universe_cohort(pd.DataFrame())
        self.assertTrue(result["consistent"])

    def test_observed_counts_are_reported(self):
        result = _universe_cohort(_performance(10, split_universe_after=6))
        self.assertEqual(sum(result["observed"].values()), 10)


class MixedUniverseBlocksTuning(unittest.TestCase):
    def test_sixty_completed_but_mixed_universe_is_not_allowed(self):
        analysis = build_episode_analysis(
            _performance(70, split_universe_after=40)
        )
        maturity = analysis.summary["maturity"]
        self.assertGreaterEqual(maturity["completed_r"], 60)
        self.assertFalse(maturity["parameter_tuning_allowed"])
        self.assertTrue(maturity["blocked_by_universe"])

    def test_sixty_completed_with_single_universe_is_allowed(self):
        analysis = build_episode_analysis(_performance(70))
        maturity = analysis.summary["maturity"]
        self.assertGreaterEqual(maturity["completed_r"], 60)
        self.assertTrue(maturity["parameter_tuning_allowed"])
        self.assertFalse(maturity["blocked_by_universe"])

    def test_segments_are_not_ready_when_universe_is_mixed(self):
        analysis = build_episode_analysis(
            _performance(70, split_universe_after=40)
        )
        for row in analysis.summary["by_selected_leg"]:
            self.assertFalse(row["tuning_ready"])

    def test_flag_can_relax_the_requirement(self):
        with mock.patch.object(Config, "EPISODE_REQUIRE_SINGLE_UNIVERSE", False):
            analysis = build_episode_analysis(
                _performance(70, split_universe_after=40)
            )
        self.assertTrue(analysis.summary["maturity"]["parameter_tuning_allowed"])

    def test_below_minimum_is_not_labelled_universe_blocked(self):
        """樣本本來就不夠時,阻擋原因是筆數不是母體,別誤導。"""
        analysis = build_episode_analysis(
            _performance(10, split_universe_after=5)
        )
        self.assertFalse(analysis.summary["maturity"]["blocked_by_universe"])

    def test_markdown_surfaces_the_mixture(self):
        analysis = build_episode_analysis(
            _performance(70, split_universe_after=40)
        )
        self.assertIn("母體不一致", analysis.markdown)
        self.assertIn("ConfigHash", analysis.markdown)


class GateValidation(unittest.TestCase):
    def _gate_from(self, summary):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text(json.dumps(summary), encoding="utf-8")
            return load_v13_calibration_gate(path)

    def _summary(self, count, *, split=None):
        return build_episode_analysis(
            _performance(count, split_universe_after=split)
        ).summary

    def test_missing_universe_cohort_fails_closed(self):
        summary = self._summary(70)
        summary.pop("universe_cohort")
        gate = self._gate_from(summary)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["reason"], "missing_universe_cohort")
        self.assertFalse(gate["parameter_tuning_allowed"])

    def test_malformed_universe_cohort_fails_closed(self):
        summary = self._summary(70)
        summary["universe_cohort"]["consistent"] = "yes"
        gate = self._gate_from(summary)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["reason"], "invalid_universe_cohort")

    def test_stale_summary_universe_is_rejected(self):
        """summary 是用別套母體算的 —— 不可沿用。"""
        summary = self._summary(70)
        summary["universe_cohort"]["current"] = "ndx-98-000000000000"
        gate = self._gate_from(summary)
        self.assertFalse(gate["ok"])
        self.assertEqual(gate["reason"], "universe_version_mismatch")

    def test_mixed_universe_gate_is_valid_but_refuses_tuning(self):
        """混合母體不是壞資料,是明確的『不可調參』—— gate 仍 ok。"""
        gate = self._gate_from(self._summary(70, split=40))
        self.assertTrue(gate["ok"])
        self.assertFalse(gate["parameter_tuning_allowed"])
        self.assertTrue(gate["blocked_by_universe"])

    def test_consistent_universe_allows_tuning(self):
        gate = self._gate_from(self._summary(70))
        self.assertTrue(gate["ok"])
        self.assertTrue(gate["parameter_tuning_allowed"])
        self.assertFalse(gate["blocked_by_universe"])

    def test_gate_carries_universe_detail(self):
        gate = self._gate_from(self._summary(70, split=40))
        self.assertEqual(gate["universe_cohort"]["distinct"], 2)


class WeeklyMessageRendering(unittest.TestCase):
    def _message(self, gate):
        aggregate = {
            "daily": [{"n": 96, "ge7": 1, "eq6": 0, "eq5": 0, "b34": 2, "warn": 1}],
            "total_ge7": 1,
            "top5": [],
            "eq6_regulars": [],
            "warn_regulars": [],
            "dip": {"available": False},
        }
        return build_message(
            aggregate, {"ok": False}, {"ok": False},
            "2026-07-20", "2026-07-26",
            v13_gate=gate,
        )

    def test_universe_block_is_explained_in_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            summary = build_episode_analysis(
                _performance(70, split_universe_after=40)
            ).summary
            path.write_text(json.dumps(summary), encoding="utf-8")
            gate = load_v13_calibration_gate(path)

        message = self._message(gate)
        self.assertIn("選股母體不一致", message)
        self.assertIn("SCAN_POOL", message)
        self.assertNotIn("可啟動參數分析", message)


class RealCohortIsCurrentlyClean(unittest.TestCase):
    def test_shipped_episode_file_uses_a_single_universe(self):
        """回歸守門:正式資料現在是乾淨的,將來變髒要有人發現。"""
        path = Path(__file__).resolve().parents[1] / "reports" / "shadow_episodes.csv"
        if not path.exists():
            self.skipTest("shadow_episodes.csv 不存在")
        episodes = pd.read_csv(path)
        if "UniverseVersion" not in episodes.columns:
            self.skipTest("舊版 episode 檔無 UniverseVersion 欄")
        self.assertEqual(episodes["UniverseVersion"].nunique(), 1)


if __name__ == "__main__":
    unittest.main()
