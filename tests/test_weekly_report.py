import unittest
import sys
import types
from unittest.mock import Mock, patch

# Codex 隨附的最小 Python 不含 requests；本測試只驗證純計算與文案，
# 不會呼叫 GitHub/Notion HTTP 路徑。
sys.modules.setdefault("requests", types.SimpleNamespace())

import weekly_report
from weekly_report import (
    build_message,
    calibration_engine,
    notion_calibration_stats,
    summarize_calibration_rows,
    v12_calibration_ready,
)


def _row(status, r=None, stop=False, dist="🎯 甜點價", gap=0):
    return {
        "status": status,
        "engine": calibration_engine(status),
        "r": r,
        "stop": stop,
        "dist": dist,
        "gap": gap,
        "d1": None,
        "d3": None,
        "d5": None,
    }


def _calibration(rows):
    result = {"ok": True, **summarize_calibration_rows(rows)}
    result["engine_stats"] = {
        engine: summarize_calibration_rows(
            [row for row in rows if row["engine"] == engine]
        )
        for engine in ("v1_2", "legacy", "unclassified")
    }
    return result


def _aggregate():
    return {
        "daily": [
            {"n": 96, "ge7": 1, "eq6": 0, "eq5": 0, "b34": 2, "warn": 1}
        ],
        "total_ge7": 1,
        "top5": [],
        "eq6_regulars": [],
        "warn_regulars": [],
        "dip": {"available": False},
    }


class CalibrationEngineTests(unittest.TestCase):
    def test_classifies_status_prefixes(self):
        self.assertEqual("v1_2", calibration_engine("📐 盤整甜點位｜量縮低接"))
        self.assertEqual("v1_2", calibration_engine("📉 低檔超賣反彈"))
        self.assertEqual("v1_2", calibration_engine("🌤️ 守均線拉回"))
        self.assertEqual("legacy", calibration_engine("🔥 靈魂吸籌"))
        self.assertEqual("unclassified", calibration_engine("📈 季營收成長"))

    def test_summarizes_only_rows_with_r_as_finalized(self):
        stats = summarize_calibration_rows(
            [
                _row("📐 盤整甜點位", r=-1, stop=True, gap=-8),
                _row("📐 盤整甜點位"),
                _row("📉 低檔超賣反彈", r=0.5),
            ]
        )

        self.assertEqual(3, stats["n_total"])
        self.assertEqual(2, stats["n_r"])
        self.assertEqual("legacy-v0", stats["measurement_version"])
        self.assertAlmostEqual(-0.25, stats["r_mean"])
        self.assertAlmostEqual(0.5, stats["win_rate"])
        self.assertAlmostEqual(0.5, stats["stop_rate"])


class CalibrationGateTests(unittest.TestCase):
    def test_legacy_samples_do_not_unlock_v12_gate(self):
        rows = (
            [_row("🔥 靈魂吸籌", r=-0.263) for _ in range(20)]
            + [_row("🔥 靈魂吸籌") for _ in range(24)]
            + [_row("📐 盤整甜點位", r=-1, stop=True) for _ in range(2)]
            + [_row("📐 盤整甜點位") for _ in range(92)]
        )
        calib = _calibration(rows)

        self.assertEqual(22, calib["n_r"])
        self.assertEqual(2, calib["engine_stats"]["v1_2"]["n_r"])
        self.assertFalse(v12_calibration_ready(calib))

        message = build_message(
            _aggregate(),
            {"ok": True, "week_count": 44, "total_count": 138},
            calib,
            "2026-07-20",
            "2026-07-26",
        )
        self.assertIn("V1.2.0 已定案 <b>2</b>/94 筆", message)
        self.assertIn("V1.2.0 R 樣本累積中(2/15)", message)
        self.assertNotIn("D8 門檻已過", message)

    def test_v12_gate_unlocks_at_fifteen_finalized_rows(self):
        rows = [_row("📐 盤整甜點位", r=0.1) for _ in range(15)]
        calib = _calibration(rows)

        self.assertTrue(v12_calibration_ready(calib))
        message = build_message(
            _aggregate(),
            {"ok": True, "week_count": 15, "total_count": 15},
            calib,
            "2026-07-20",
            "2026-07-26",
        )
        self.assertIn("D8 門檻已過", message)

    def test_unclassified_rows_never_count_toward_v12_gate(self):
        rows = [_row("未標記策略", r=0.5) for _ in range(20)]
        calib = _calibration(rows)

        self.assertEqual(20, calib["engine_stats"]["unclassified"]["n_r"])
        self.assertEqual(0, calib["engine_stats"]["v1_2"]["n_r"])
        self.assertFalse(v12_calibration_ready(calib))


class NotionCalibrationTests(unittest.TestCase):
    def test_reads_status_rich_text_and_builds_engine_cohorts(self):
        response = Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {
            "has_more": False,
            "results": [
                {
                    "properties": {
                        "Status": {
                            "rich_text": [{"plain_text": "📐 盤整甜點位｜量縮低接"}]
                        },
                        "R值": {"number": -1},
                        "D+1報酬%": {"number": -2},
                        "D+3報酬%": {"number": None},
                        "D+5報酬%": {"number": None},
                        "是否觸發停損": {"checkbox": True},
                        "DistTag": {"select": {"name": "🎯 甜點價"}},
                        "盤前跳空%": {"number": -8},
                    }
                },
                {
                    "properties": {
                        "Status": {"rich_text": [{"plain_text": "🔥 靈魂吸籌"}]},
                        "R值": {"number": 0.5},
                        "D+1報酬%": {"number": 1},
                        "D+3報酬%": {"number": 2},
                        "D+5報酬%": {"number": 3},
                        "是否觸發停損": {"checkbox": False},
                        "DistTag": {"select": {"name": "🎯 甜點價"}},
                        "盤前跳空%": {"number": 1},
                    }
                },
            ],
        }

        with patch.dict(
            "os.environ",
            {"NOTION_TOKEN": "test-token", "NOTION_DB_ID": "test-db"},
            clear=False,
        ), patch.object(weekly_report.requests, "post", return_value=response, create=True):
            stats = notion_calibration_stats()

        self.assertTrue(stats["ok"])
        self.assertEqual(1, stats["engine_stats"]["v1_2"]["n_r"])
        self.assertEqual(1, stats["engine_stats"]["legacy"]["n_r"])
        self.assertEqual(0, stats["engine_stats"]["unclassified"]["n_total"])


if __name__ == "__main__":
    unittest.main()
