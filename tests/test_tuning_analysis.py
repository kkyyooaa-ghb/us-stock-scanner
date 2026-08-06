"""V1.3 調參分析工具測試。

這支工具的價值不在算得出平均 R,而在**不會騙人**:
  1. 閘門沒開就不得輸出結論。
  2. 切十幾個維度後,純噪音不該冒出「確認」訊號。
  3. 樣本不足的格子不得參與結論。
  4. 同日雙觸的 R 上下界分歧時要說出來,不可平均掉。
"""
import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config
from tuning_analysis import (
    analyze_tuning,
    derive_dimensions,
    render_markdown,
    _required_n,
)


def _rows(spec, *, seed=1, sd=1.0, r_upper_shift=0.0):
    """spec: {(leg, order_type): (n, true_mean)}"""
    rng = np.random.default_rng(seed)
    rows = []
    for (leg, order_type), (count, mean) in spec.items():
        for value in rng.normal(mean, sd, count):
            rows.append({
                "EpisodeStatus": "completed",
                "PlanSelectedLeg": leg,
                "CandidateLeg": leg,
                "OrderType": order_type,
                "MarketBias": "neutral",
                "V13RLower": value,
                "V13RUpper": value + r_upper_shift,
            })
    return pd.DataFrame(rows)


class AuthorizationIsAbsolute(unittest.TestCase):
    def setUp(self):
        self.frame = _rows({("leg_A", "buy_limit_zone"): (80, 0.8)})

    def test_unauthorized_withholds_findings(self):
        result = analyze_tuning(self.frame, gate_allows_tuning=False)
        self.assertFalse(result["authorized"])
        self.assertEqual(result["findings"], [])
        self.assertTrue(result["findings_withheld"])

    def test_unauthorized_still_reports_statistics(self):
        """扣住結論不等於不算 —— 工具驗證需要看到統計。"""
        result = analyze_tuning(self.frame, gate_allows_tuning=False)
        self.assertGreater(result["completed_episodes"], 0)
        self.assertTrue(result["by_bound"]["lower"])

    def test_unauthorized_markdown_carries_the_banner(self):
        markdown = render_markdown(
            analyze_tuning(self.frame, gate_allows_tuning=False)
        )
        self.assertIn("未授權", markdown)
        self.assertIn("不得據以調參", markdown)

    def test_authorized_emits_findings(self):
        result = analyze_tuning(self.frame, gate_allows_tuning=True)
        self.assertTrue(result["authorized"])
        self.assertTrue(result["findings"])
        self.assertFalse(result["findings_withheld"])


class MultipleComparisonProtection(unittest.TestCase):
    def test_pure_noise_across_many_cells_yields_no_confirmed_signal(self):
        """切很多格的純噪音,是這類工具最典型的假陽性來源。"""
        rng = np.random.default_rng(99)
        rows = []
        for leg in ("a", "b", "c"):
            for order_type in ("x", "y"):
                for bias in ("bull", "bear"):
                    for value in rng.normal(0.0, 1.0, 40):
                        rows.append({
                            "EpisodeStatus": "completed",
                            "PlanSelectedLeg": leg,
                            "CandidateLeg": leg,
                            "OrderType": order_type,
                            "MarketBias": bias,
                            "V13RLower": value,
                            "V13RUpper": value,
                        })
        result = analyze_tuning(pd.DataFrame(rows), gate_allows_tuning=True)
        self.assertGreater(result["hypotheses_tested"], 5)
        self.assertEqual(result["findings"], [])

    def test_adjusted_alpha_shrinks_with_more_cells(self):
        few = analyze_tuning(
            _rows({("leg_A", "buy_limit_zone"): (40, 0.0)}),
            gate_allows_tuning=True,
        )
        many = analyze_tuning(
            _rows({
                ("leg_A", "buy_limit_zone"): (40, 0.0),
                ("leg_B", "buy_stop_reclaim"): (40, 0.0),
                ("leg_C", "buy_limit_zone"): (40, 0.0),
            }),
            gate_allows_tuning=True,
        )
        self.assertLess(many["adjusted_alpha"], few["adjusted_alpha"])

    def test_real_edge_still_survives_adjustment(self):
        """校正不能嚴到連真訊號都殺掉。"""
        frame = _rows({
            ("leg_A", "buy_limit_zone"): (80, 1.0),
            ("leg_B", "buy_stop_reclaim"): (80, 0.0),
        }, seed=3)
        result = analyze_tuning(frame, gate_allows_tuning=True)
        levels = {f["level"] for f in result["findings"]}
        self.assertIn("leg_A", levels)
        self.assertNotIn("leg_B", levels)


class SampleSufficiency(unittest.TestCase):
    def test_cells_below_minimum_are_not_eligible(self):
        frame = _rows({("leg_A", "buy_limit_zone"): (5, 2.0)})
        result = analyze_tuning(frame, gate_allows_tuning=True)
        cell = result["by_bound"]["lower"]["PlanSelectedLeg"]["leg_A"]
        self.assertFalse(cell["eligible"])
        self.assertFalse(cell["confirmed_signal"])
        self.assertEqual(result["findings"], [])

    def test_small_cells_do_not_dilute_the_correction(self):
        """樣本不足的格子本來就不下結論,不該稀釋校正幅度。"""
        frame = _rows({
            ("leg_A", "buy_limit_zone"): (40, 0.0),
            ("leg_tiny", "buy_stop_reclaim"): (2, 0.0),
        })
        result = analyze_tuning(frame, gate_allows_tuning=True)
        eligible = [
            level
            for level, stats
            in result["by_bound"]["lower"]["PlanSelectedLeg"].items()
            if stats["eligible"]
        ]
        self.assertEqual(eligible, ["leg_A"])

    def test_required_n_grows_with_dispersion(self):
        tight = _required_n(np.random.default_rng(1).normal(0, 0.5, 50))
        wide = _required_n(np.random.default_rng(1).normal(0, 2.0, 50))
        self.assertLess(tight, wide)

    def test_required_n_is_none_when_undecidable(self):
        self.assertIsNone(_required_n(np.array([1.0])))

    def test_zero_completed_reports_nothing_testable(self):
        frame = pd.DataFrame([{
            "EpisodeStatus": "open",
            "PlanSelectedLeg": "leg_A",
            "V13RLower": None,
            "V13RUpper": None,
        }])
        result = analyze_tuning(frame, gate_allows_tuning=True)
        self.assertEqual(result["completed_episodes"], 0)
        self.assertEqual(result["hypotheses_tested"], 0)
        self.assertIn("尚無任何格達到最低樣本", render_markdown(result))


class AmbiguousRBounds(unittest.TestCase):
    def test_divergent_bounds_are_flagged_not_averaged(self):
        """同日雙觸:下界有 edge、上界沒有 → 結論取決於盤中順序,必須標出。"""
        frame = _rows(
            {("leg_A", "buy_limit_zone"): (80, 1.0)}, seed=5, r_upper_shift=-1.0
        )
        result = analyze_tuning(frame, gate_allows_tuning=True)
        finding = next(
            f for f in result["findings"] if f["level"] == "leg_A"
        )
        self.assertNotEqual(
            finding["mean_r_lower_bound"], finding["mean_r_upper_bound"]
        )

    def test_agreeing_bounds_are_robust(self):
        frame = _rows({("leg_A", "buy_limit_zone"): (80, 1.0)}, seed=5)
        result = analyze_tuning(frame, gate_allows_tuning=True)
        finding = next(f for f in result["findings"] if f["level"] == "leg_A")
        self.assertTrue(finding["robust_across_r_bounds"])


class Determinism(unittest.TestCase):
    def test_same_input_gives_same_answer(self):
        """bootstrap 有隨機性;固定種子讓同一份資料每次結論一致。"""
        frame = _rows({("leg_A", "buy_limit_zone"): (60, 0.5)})
        first = analyze_tuning(frame, gate_allows_tuning=True)
        second = analyze_tuning(frame, gate_allows_tuning=True)
        self.assertEqual(first["by_bound"], second["by_bound"])
        self.assertEqual(first["findings"], second["findings"])


class DimensionDerivation(unittest.TestCase):
    def test_vix_and_priority_buckets_are_added(self):
        frame = pd.DataFrame([
            {"VixLevel": 12.0, "PriorityPostTheme": 7},
            {"VixLevel": 22.0, "PriorityPostTheme": 13},
        ])
        out = derive_dimensions(frame)
        self.assertEqual(list(out["VixBucket"]), ["vix<15", "vix20-25"])
        self.assertEqual(list(out["PriorityBucket"]), ["P7", "P12+"])

    def test_theme_influence_is_normalised(self):
        frame = pd.DataFrame([
            {"CrossedThresholdDueToTheme": True},
            {"CrossedThresholdDueToTheme": False},
        ])
        out = derive_dimensions(frame)
        self.assertEqual(list(out["theme_crossed_threshold"]), ["yes", "no"])

    def test_original_columns_are_untouched(self):
        frame = _rows({("leg_A", "buy_limit_zone"): (3, 0.0)})
        before = frame.copy()
        derive_dimensions(frame)
        pd.testing.assert_frame_equal(frame, before)

    def test_missing_dimensions_are_reported_not_invented(self):
        frame = _rows({("leg_A", "buy_limit_zone"): (25, 0.0)})
        result = analyze_tuning(frame, gate_allows_tuning=True)
        self.assertIn("PreGapStatus", result["dimensions_missing"])
        self.assertNotIn("PreGapStatus", result["dimensions_analysed"])


class RealDataStaysLocked(unittest.TestCase):
    def test_shipped_episodes_produce_no_findings_today(self):
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "reports" / "shadow_episodes.csv"
        if not path.exists():
            self.skipTest("shadow_episodes.csv 不存在")
        result = analyze_tuning(
            pd.read_csv(path), gate_allows_tuning=True
        )
        # 目前只有 2 筆 completed,遠低於每格 20
        self.assertLess(result["completed_episodes"], Config.EPISODE_SEGMENT_MIN_COMPLETED)
        self.assertEqual(result["findings"], [])


if __name__ == "__main__":
    unittest.main()
