import json
import unittest
import sys
import types
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

from config import Config
# Codex 隨附的最小 Python 不含 requests；本測試只驗證純計算與文案，
# 不會呼叫 GitHub/Notion HTTP 路徑。
sys.modules.setdefault("requests", types.SimpleNamespace())

import weekly_report
from snapshot_schema import SNAPSHOT_COLUMNS, write_failure_snapshot
from weekly_report import (
    aggregate,
    build_message,
    calibration_engine,
    legacy_v12_sample_complete,
    load_v13_calibration_gate,
    notion_calibration_stats,
    refresh_v13_episode_artifacts,
    summarize_calibration_rows,
    select_daily_artifacts,
    write_report_files,
)
from trade_plan import strategy_config_hash, universe_version


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


class TopFiveOnlyListsActionableNames(unittest.TestCase):
    """2026-08-05 的 EA 以全週最高分 19.83 排在 Top5 首位,卻無腿別、
    無交易計畫、Priority=1。掛在「本週最高分」下會被誤讀為最佳標的。"""

    def _day(self, rows):
        return [("2026-08-05", pd.DataFrame(rows))]

    def _row(self, ticker, priority, score, leg):
        return {
            "Ticker": ticker, "Priority": priority, "Score": score,
            "DistTag": "🎯 甜點價", "YoY": 0.1, "SelectedLeg": leg,
        }

    def test_high_score_without_a_leg_is_excluded(self):
        days = self._day([
            self._row("EA", 1, 19.83, "none"),
            self._row("CEG", 16, 16.5, "consolidation_dip"),
            self._row("AVGO", 15, 15.7, "consolidation_dip"),
        ])
        top5 = aggregate(days)["top5"]
        self.assertNotIn("EA", [r["Ticker"] for r in top5])
        self.assertEqual(top5[0]["Ticker"], "CEG")

    def test_actionable_names_keep_score_ordering(self):
        days = self._day([
            self._row("CEG", 16, 16.5, "consolidation_dip"),
            self._row("ALNY", 13, 15.5, "oversold_bounce"),
        ])
        top5 = aggregate(days)["top5"]
        self.assertEqual([r["Ticker"] for r in top5], ["CEG", "ALNY"])

    def test_missing_column_does_not_filter_old_snapshots(self):
        """舊 schema 沒有 SelectedLeg —— 無從判斷就不過濾,不臆測。"""
        frame = pd.DataFrame([{
            "Ticker": "OLD", "Priority": 9, "Score": 11.0,
            "DistTag": "🎯 甜點價", "YoY": 0.1,
        }])
        top5 = aggregate([("2026-07-20", frame)])["top5"]
        self.assertEqual([r["Ticker"] for r in top5], ["OLD"])

    def test_all_unactionable_falls_back_rather_than_empty(self):
        days = self._day([
            self._row("EA", 1, 19.83, "none"),
            self._row("XYZ", 2, 12.0, "none"),
        ])
        self.assertTrue(aggregate(days)["top5"])


def _universe_cohort(consistent=True, *, current=None):
    current = current or universe_version()
    if consistent:
        return {
            "current": current,
            "observed": {current: 30},
            "distinct": 1,
            "mixed": False,
            "matches_current": True,
            "consistent": True,
            "reason": None,
        }
    return {
        "current": current,
        "observed": {current: 20, "ndx-98-deadbeefcafe": 10},
        "distinct": 2,
        "mixed": True,
        "matches_current": False,
        "consistent": False,
        "reason": "multiple_universe_versions",
    }


def _v13_summary(completed=2, *, tuning_allowed=None, universe=None):
    minimum = Config.EPISODE_TUNING_MIN_COMPLETED
    target = Config.EPISODE_TUNING_TARGET
    universe = _universe_cohort() if universe is None else universe
    if tuning_allowed is None:
        tuning_allowed = completed >= minimum and universe["consistent"]
    if completed >= target:
        stage = "target_reached"
    elif completed >= minimum:
        stage = "minimum_reached"
    else:
        stage = "collecting"
    return {
        "schema_version": Config.SNAPSHOT_SCHEMA_VERSION,
        "trade_plan_version": Config.TRADE_PLAN_VERSION,
        "measurement_version": Config.SHADOW_MEASUREMENT_VERSION,
        "selection_cohort": {
            "signal_engine_version": Config.SIGNAL_ENGINE_VERSION,
            "config_hash": strategy_config_hash(),
        },
        "maturity": {
            "completed_r": completed,
            "minimum_completed": minimum,
            "target_completed": target,
            "remaining_to_minimum": max(minimum - completed, 0),
            "remaining_to_target": max(target - completed, 0),
            "stage": stage,
            "parameter_tuning_allowed": tuning_allowed,
        },
        "universe_cohort": universe,
        "by_selected_leg": [],
        "by_order_type": [],
    }


def _v13_gate(completed=2):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "summary.json"
        path.write_text(
            json.dumps(_v13_summary(completed)),
            encoding="utf-8",
        )
        return load_v13_calibration_gate(path)


class SnapshotArchiveTests(unittest.TestCase):
    def test_new_snapshots_are_archived_through_canonical_writer(self):
        frame = pd.DataFrame([{
            "Ticker": "TEST",
            "Priority": 7,
            "Score": 7.5,
            "UniverseExpectedCount": 1,
            "UniverseDisposition": "processed",
            "DollarVolumeMedian20": 500_000_000.0,
            "SnapshotSchemaVersion": Config.SNAPSHOT_SCHEMA_VERSION,
            "SnapshotAsOfET": "2026-07-27T09:00:00-04:00",
            "DataBarDate": "2026-07-24",
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
            "PlanEarliestEntryDate": "2026-07-27",
        }])
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp) / "reports"
            weekly_report.archive_daily_csv(
                frame,
                "2026-07-27",
                report_root=report_root,
            )
            archived = pd.read_csv(
                report_root / "daily" / "2026-07-27.csv",
                encoding="utf-8-sig",
            )

        self.assertEqual(list(SNAPSHOT_COLUMNS), list(archived.columns))

    def test_schema_upgrade_week_archives_both_generations(self):
        """升版當週:舊 artifact 原樣保存,新 artifact 走 canonical writer。

        這是最容易在升版當下弄丟資料的一天 —— 若舊版被硬套現行契約,
        整份週報會中止,而那份 forward 快照是不可再生的。
        """
        def _row(schema_version, bar_date):
            return {
                "Ticker": "TEST",
                "Priority": 7,
                "Score": 7.5,
                "UniverseExpectedCount": 1,
                "UniverseDisposition": "processed",
                "DollarVolumeMedian20": 500_000_000.0,
                "SnapshotSchemaVersion": schema_version,
                "SnapshotAsOfET": f"{bar_date}T09:00:00-04:00",
                "DataBarDate": bar_date,
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
                "PlanEarliestEntryDate": bar_date,
            }

        legacy = pd.DataFrame([_row("v1.3.1", "2026-07-27")])
        current = pd.DataFrame(
            [_row(Config.SNAPSHOT_SCHEMA_VERSION, "2026-07-28")]
        )
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp) / "reports"
            weekly_report.archive_daily_csv(legacy, "2026-07-27", report_root)
            weekly_report.archive_daily_csv(current, "2026-07-28", report_root)

            old = pd.read_csv(
                report_root / "daily" / "2026-07-27.csv", encoding="utf-8-sig"
            )
            new = pd.read_csv(
                report_root / "daily" / "2026-07-28.csv", encoding="utf-8-sig"
            )

        # 舊版:欄位與版本標記都保持原樣,絕不被今天的 schema 改寫
        self.assertEqual(list(legacy.columns), list(old.columns))
        self.assertEqual(["v1.3.1"], list(old["SnapshotSchemaVersion"]))
        # 新版:走 canonical writer,補齊完整欄位
        self.assertEqual(list(SNAPSHOT_COLUMNS), list(new.columns))
        self.assertEqual(
            [Config.SNAPSHOT_SCHEMA_VERSION],
            list(new["SnapshotSchemaVersion"]),
        )

    def test_later_failure_does_not_replace_successful_premarket_snapshot(self):
        premarket = pd.DataFrame([{
            "SnapshotRecordType": "data",
            "Priority": 7,
            "ScanSession": "premarket",
        }])
        failure = pd.DataFrame([{
            "SnapshotRecordType": "control",
            "SnapshotRunStatus": "error",
        }])
        candidates = [
            {
                "id": 1,
                "et_date": "2026-07-27",
                "created_at": weekly_report.datetime(
                    2026, 7, 27, 13, 5, tzinfo=weekly_report.timezone.utc
                ),
                "frame": premarket,
            },
            {
                "id": 2,
                "et_date": "2026-07-27",
                "created_at": weekly_report.datetime(
                    2026, 7, 27, 18, 0, tzinfo=weekly_report.timezone.utc
                ),
                "frame": failure,
            },
        ]

        selected = select_daily_artifacts(candidates)

        self.assertEqual([1], [item["id"] for item in selected])

    def test_preopen_success_outranks_later_after_open_rerun(self):
        preopen = pd.DataFrame([{
            "SnapshotRecordType": "data",
            "Priority": 7,
            "ScanSession": "preopen",
        }])
        after_open = pd.DataFrame([{
            "SnapshotRecordType": "data",
            "Priority": 8,
            "ScanSession": "after_open",
        }])
        candidates = [
            {
                "id": 3,
                "et_date": "2026-07-27",
                "created_at": weekly_report.datetime(
                    2026, 7, 27, 7, 30, tzinfo=weekly_report.timezone.utc
                ),
                "frame": preopen,
            },
            {
                "id": 4,
                "et_date": "2026-07-27",
                "created_at": weekly_report.datetime(
                    2026, 7, 27, 15, 0, tzinfo=weekly_report.timezone.utc
                ),
                "frame": after_open,
            },
        ]

        selected = select_daily_artifacts(candidates)

        self.assertEqual([3], [item["id"] for item in selected])

    def test_control_only_day_is_selected_and_permanently_archived(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact_path = root / "artifact.csv"
            write_failure_snapshot(
                artifact_path,
                "upstream unavailable",
                error_type="DataUnavailable",
            )
            control = pd.read_csv(artifact_path, encoding="utf-8-sig")
            candidates = [{
                "id": 9,
                "et_date": "2026-07-27",
                "created_at": weekly_report.datetime(
                    2026, 7, 27, 13, 5, tzinfo=weekly_report.timezone.utc
                ),
                "frame": control,
            }]

            selected = select_daily_artifacts(candidates)
            weekly_report.archive_daily_csv(
                selected[0]["frame"],
                selected[0]["et_date"],
                report_root=root / "reports",
            )
            archived = pd.read_csv(
                root / "reports" / "daily" / "2026-07-27.csv",
                encoding="utf-8-sig",
            )

        self.assertEqual([9], [item["id"] for item in selected])
        self.assertEqual("control", archived.iloc[0]["SnapshotRecordType"])
        self.assertEqual("error", archived.iloc[0]["SnapshotRunStatus"])


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
    def test_legacy_samples_remain_historical_and_do_not_unlock_v13_gate(self):
        rows = (
            [_row("🔥 靈魂吸籌", r=-0.263) for _ in range(20)]
            + [_row("🔥 靈魂吸籌") for _ in range(24)]
            + [_row("📐 盤整甜點位", r=-1, stop=True) for _ in range(2)]
            + [_row("📐 盤整甜點位") for _ in range(92)]
        )
        calib = _calibration(rows)

        self.assertEqual(22, calib["n_r"])
        self.assertEqual(2, calib["engine_stats"]["v1_2"]["n_r"])
        self.assertFalse(legacy_v12_sample_complete(calib))

        message = build_message(
            _aggregate(),
            {"ok": True, "week_count": 44, "total_count": 138},
            calib,
            "2026-07-20",
            "2026-07-26",
            _v13_gate(2),
        )
        self.assertIn("V1.3 completed-R <b>2</b>/60", message)
        self.assertIn("禁止調參", message)
        self.assertIn("V1.2.x legacy-v0:2/94", message)
        self.assertNotIn("D8 門檻已過", message)

    def test_fifteen_legacy_rows_do_not_unlock_v13_tuning(self):
        rows = [_row("📐 盤整甜點位", r=0.1) for _ in range(15)]
        calib = _calibration(rows)

        self.assertTrue(legacy_v12_sample_complete(calib))
        message = build_message(
            _aggregate(),
            {"ok": True, "week_count": 15, "total_count": 15},
            calib,
            "2026-07-20",
            "2026-07-26",
            _v13_gate(2),
        )
        self.assertIn("V1.2.x legacy-v0:15/15", message)
        self.assertIn("V1.3 completed-R <b>2</b>/60", message)
        self.assertIn("禁止調參", message)
        self.assertNotIn("可依上方", message)

    def test_unclassified_rows_never_count_toward_v12_gate(self):
        rows = [_row("未標記策略", r=0.5) for _ in range(20)]
        calib = _calibration(rows)

        self.assertEqual(20, calib["engine_stats"]["unclassified"]["n_r"])
        self.assertEqual(0, calib["engine_stats"]["v1_2"]["n_r"])
        self.assertFalse(legacy_v12_sample_complete(calib))


class V13CalibrationGateTests(unittest.TestCase):
    def _load(self, payload):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_v13_calibration_gate(path)

    def test_validates_current_cohort_and_locks_before_sixty(self):
        gate = self._load(_v13_summary(59))

        self.assertTrue(gate["ok"])
        self.assertEqual("collecting", gate["status"])
        self.assertEqual(59, gate["completed_r"])
        self.assertFalse(gate["parameter_tuning_allowed"])

    def test_minimum_and_target_stages_are_reported_without_auto_tuning(self):
        minimum = self._load(_v13_summary(60))
        target = self._load(_v13_summary(100))

        self.assertEqual("minimum_reached", minimum["status"])
        self.assertTrue(minimum["parameter_tuning_allowed"])
        self.assertEqual("target_reached", target["status"])
        self.assertTrue(target["parameter_tuning_allowed"])

    def test_missing_corrupt_or_mismatched_summary_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = load_v13_calibration_gate(Path(tmp) / "missing.json")
            corrupt_path = Path(tmp) / "corrupt.json"
            corrupt_path.write_text("{", encoding="utf-8")
            corrupt = load_v13_calibration_gate(corrupt_path)

        mismatched = _v13_summary(100)
        mismatched["selection_cohort"]["config_hash"] = "wrong"
        mismatch = self._load(mismatched)

        self.assertEqual("summary_missing", missing["reason"])
        self.assertEqual("summary_invalid_json", corrupt["reason"])
        self.assertEqual("cohort_mismatch", mismatch["reason"])
        for gate in (missing, corrupt, mismatch):
            self.assertFalse(gate["ok"])
            self.assertFalse(gate["parameter_tuning_allowed"])

    def test_inconsistent_maturity_fails_closed(self):
        gate = self._load(_v13_summary(2, tuning_allowed=True))

        self.assertFalse(gate["ok"])
        self.assertEqual("invalid_maturity", gate["reason"])

    def test_segment_needs_twenty_completed_and_overall_minimum(self):
        payload = _v13_summary(60)
        payload["by_selected_leg"] = [{
            "segment": "oversold_bounce",
            "completed_r": 19,
            "segment_min_completed": Config.EPISODE_SEGMENT_MIN_COMPLETED,
            "tuning_ready": False,
        }]

        gate = self._load(payload)

        self.assertTrue(gate["ok"])
        self.assertFalse(gate["by_selected_leg"][0]["tuning_ready"])

    def test_message_shows_fail_closed_reason(self):
        message = build_message(
            _aggregate(),
            {"ok": False},
            {"ok": False},
            "2026-07-20",
            "2026-07-26",
            {
                "ok": False,
                "status": "blocked",
                "reason": "cohort_mismatch",
                "parameter_tuning_allowed": False,
            },
        )

        self.assertIn("cohort_mismatch", message)
        self.assertIn("fail-closed", message)
        self.assertIn("禁止調參", message)

    def test_zero_scan_week_still_exposes_fail_closed_gate(self):
        message = build_message(
            {"daily": []},
            {"ok": False},
            {"ok": False},
            "2026-07-20",
            "2026-07-26",
            {
                "ok": False,
                "reason": "summary_missing",
                "parameter_tuning_allowed": False,
            },
        )

        self.assertIn("0 次", message)
        self.assertIn("summary_missing", message)
        self.assertIn("fail-closed 禁止調參", message)

    def test_report_json_persists_the_validated_gate(self):
        gate = _v13_gate(2)
        message = build_message(
            _aggregate(),
            {"ok": False},
            {"ok": False},
            "2026-07-20",
            "2026-07-26",
            gate,
        )
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp) / "reports"
            write_report_files(
                message,
                _aggregate(),
                {"ok": False},
                {"ok": False},
                "2026-07-20",
                "2026-07-26",
                [],
                gate,
                report_root=report_root,
            )
            payload = json.loads(
                (report_root / "latest.json").read_text(encoding="utf-8")
            )

        # 4:gate 新增 projection(達標日預估,純資訊)
        self.assertEqual(4, payload["schema_version"])
        self.assertEqual(2, payload["v13_calibration_gate"]["completed_r"])
        self.assertFalse(
            payload["v13_calibration_gate"]["parameter_tuning_allowed"]
        )
        # projection 欄位必須存在(可為 None),讓下游知道這版有這個概念
        self.assertIn("projection", payload["v13_calibration_gate"])

    def test_refresh_runs_shadow_then_episode_after_daily_archive_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp) / "reports"
            daily = report_root / "daily" / "2026-07-27.csv"
            daily.parent.mkdir(parents=True)
            daily.write_text("Ticker\nTEST\n", encoding="utf-8")
            calls = []

            def track(argv):
                calls.append(("shadow", argv))
                return 0

            def episodes(argv):
                calls.append(("episodes", argv))
                return 0

            with patch("track_shadow_performance.main", side_effect=track), patch(
                "build_shadow_episodes.main", side_effect=episodes
            ):
                result = refresh_v13_episode_artifacts(report_root)

        self.assertTrue(result["ok"])
        self.assertEqual(["shadow", "episodes"], [name for name, _ in calls])
        self.assertIn(str(daily), calls[0][1])

    def test_refresh_failure_does_not_continue_to_episode_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            report_root = Path(tmp) / "reports"
            daily = report_root / "daily" / "2026-07-27.csv"
            daily.parent.mkdir(parents=True)
            daily.write_text("Ticker\nTEST\n", encoding="utf-8")
            with patch("track_shadow_performance.main", return_value=1), patch(
                "build_shadow_episodes.main"
            ) as episodes:
                result = refresh_v13_episode_artifacts(report_root)

        self.assertFalse(result["ok"])
        self.assertEqual("shadow_refresh_failed", result["reason"])
        episodes.assert_not_called()


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
