import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from config import Config
from snapshot_health import (
    evaluate_snapshot_health,
    main as health_main,
    render_health_markdown,
)
from snapshot_schema import write_snapshot


GIT_SHA = "a" * 40


def _data_row(ticker="AAA", expected=2, **overrides):
    row = {
        "SnapshotRecordType": "data",
        "UniverseExpectedCount": expected,
        "UniverseDisposition": "processed",
        "UniverseExclusionReason": "",
        "DollarVolumeMedian20": 500_000_000.0,
        "Ticker": ticker,
        "Priority": 6,
        "Score": 6.5,
        "SnapshotAsOfET": "2026-07-28T09:01:00-04:00",
        "DataBarDate": "2026-07-27",
        "ScanSession": "premarket",
        "ScanAfterOpen": 0,
        "SnapshotTimingSource": "xnys",
        "PreGapPct": 1.0,
        "PreGapStatus": "available",
        "PreMarketPrice": 101.0,
        "PreMarketQuoteTimeET": "2026-07-28T09:00:00-04:00",
        "PreGapReferencePrice": 100.0,
        "PreGapReferenceDate": "2026-07-27",
        "PreGapReferenceBasis": "signal_bar_close_auto_adjusted",
        "PreGapDefinitionVersion": Config.PREGAP_DEFINITION_VERSION,
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "MeasurementVersion": Config.MEASUREMENT_VERSION,
        "GitCommitSha": GIT_SHA,
        "ConfigHash": "test-config",
        "UniverseVersion": "test-universe",
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


def _audit_row(ticker="BBB", expected=2, **overrides):
    row = {
        "SnapshotRecordType": "universe_audit",
        "UniverseExpectedCount": expected,
        "UniverseDisposition": "excluded",
        "UniverseExclusionReason": "stale_bar",
        "Ticker": ticker,
        "SnapshotAsOfET": "2026-07-28T09:01:00-04:00",
        "ScanSession": "premarket",
        "ScanAfterOpen": 0,
        "SnapshotTimingSource": "xnys",
        "PreGapStatus": "not_attempted",
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "MeasurementVersion": Config.MEASUREMENT_VERSION,
        "GitCommitSha": GIT_SHA,
        "ConfigHash": "test-config",
        "UniverseVersion": "test-universe",
        "TradePlanStatus": "not_applicable",
        "TradePlanVersion": Config.TRADE_PLAN_VERSION,
        "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
    }
    row.update(overrides)
    return row


class SnapshotHealthTests(unittest.TestCase):
    def test_clean_snapshot_is_ok(self):
        frame = pd.DataFrame([
            _data_row("AAA"),
            _data_row("BBB"),
        ])

        result = evaluate_snapshot_health(
            frame,
            expected_git_sha=GIT_SHA,
            expected_tickers=["AAA", "BBB"],
        )

        self.assertEqual("ok", result["status"])
        self.assertTrue(result["usable_for_shadow"])
        self.assertEqual(2, result["metrics"]["universe"]["processed"])
        self.assertEqual(1.0, result["metrics"]["pregap"]["coverage_rate"])

    def test_excluded_ticker_is_a_usable_warning(self):
        frame = pd.DataFrame([
            _data_row("AAA"),
            _audit_row("BBB"),
        ])

        result = evaluate_snapshot_health(
            frame,
            expected_git_sha=GIT_SHA,
            expected_tickers=["AAA", "BBB"],
        )

        self.assertEqual("warning", result["status"])
        self.assertTrue(result["usable_for_shadow"])
        self.assertEqual(
            ["universe_not_processed"],
            [finding["code"] for finding in result["warnings"]],
        )
        self.assertEqual(
            {"stale_bar": 1},
            result["metrics"]["universe"]["reasons"],
        )

    def test_top10_stale_quote_is_visible_but_not_blocking(self):
        frame = pd.DataFrame([
            _data_row(
                "AAA",
                Priority=9,
                PreGapPct=pd.NA,
                PreGapStatus="stale_quote",
                PreMarketPrice=101.0,
            ),
            _data_row("BBB"),
        ])

        result = evaluate_snapshot_health(
            frame,
            expected_tickers=["AAA", "BBB"],
        )

        self.assertEqual("warning", result["status"])
        self.assertTrue(result["usable_for_shadow"])
        self.assertEqual(
            ["AAA"],
            result["metrics"]["selection"]["top10_pregap_unavailable"],
        )
        self.assertEqual(
            {"pregap_unavailable", "top10_pregap_unavailable"},
            {finding["code"] for finding in result["warnings"]},
        )

    def test_universe_membership_mismatch_is_blocked(self):
        frame = pd.DataFrame([
            _data_row("AAA"),
            _data_row("CCC"),
        ])

        result = evaluate_snapshot_health(
            frame,
            expected_tickers=["AAA", "BBB"],
        )

        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["usable_for_shadow"])
        self.assertIn(
            "universe_membership_mismatch",
            [finding["code"] for finding in result["errors"]],
        )

    def test_pregap_formula_mismatch_is_blocked(self):
        frame = pd.DataFrame([
            _data_row("AAA", PreGapPct=9.0),
            _data_row("BBB"),
        ])

        result = evaluate_snapshot_health(
            frame,
            expected_tickers=["AAA", "BBB"],
        )

        self.assertEqual("blocked", result["status"])
        self.assertIn(
            "pregap_formula_mismatch",
            [finding["code"] for finding in result["errors"]],
        )

    def test_pregap_reference_date_mismatch_is_blocked(self):
        frame = pd.DataFrame([
            _data_row("AAA", PreGapReferenceDate="2026-07-24"),
            _data_row("BBB"),
        ])

        result = evaluate_snapshot_health(
            frame,
            expected_tickers=["AAA", "BBB"],
        )

        self.assertIn(
            "pregap_reference_date_mismatch",
            [finding["code"] for finding in result["errors"]],
        )

    def test_expected_git_sha_mismatch_is_blocked(self):
        frame = pd.DataFrame([
            _data_row("AAA"),
            _data_row("BBB"),
        ])

        result = evaluate_snapshot_health(
            frame,
            expected_git_sha="b" * 40,
            expected_tickers=["AAA", "BBB"],
        )

        self.assertIn(
            "git_sha_mismatch",
            [finding["code"] for finding in result["errors"]],
        )

    def test_skipped_control_snapshot_is_not_a_failure(self):
        frame = pd.DataFrame([{
            "SnapshotRecordType": "control",
            "SnapshotRunStatus": "skipped",
            "SnapshotErrorType": "NonTradingSession",
            "SnapshotErrorMessage": "holiday",
            "SnapshotAsOfET": "2026-07-04T09:00:00-04:00",
            "ScanSession": "non_session",
        }])

        result = evaluate_snapshot_health(frame)

        self.assertEqual("skipped", result["status"])
        self.assertFalse(result["usable_for_shadow"])
        self.assertEqual([], result["errors"])

    def test_error_control_snapshot_is_blocked(self):
        frame = pd.DataFrame([{
            "SnapshotRecordType": "control",
            "SnapshotRunStatus": "error",
            "SnapshotErrorType": "DataUnavailable",
            "SnapshotErrorMessage": "no rows",
            "SnapshotAsOfET": "2026-07-28T09:00:00-04:00",
            "ScanSession": "error",
        }])

        result = evaluate_snapshot_health(frame)

        self.assertEqual("blocked", result["status"])
        self.assertFalse(result["usable_for_shadow"])

    def test_cli_writes_json_and_markdown(self):
        rows = [
            _data_row(
                ticker,
                expected=len(Config.SCAN_POOL),
                GitCommitSha=GIT_SHA,
            )
            for ticker in Config.SCAN_POOL
        ]
        with tempfile.TemporaryDirectory() as tmp:
            snapshot = Path(tmp) / "scan_result.csv"
            json_path = Path(tmp) / "snapshot_health.json"
            markdown_path = Path(tmp) / "summary.md"
            write_snapshot(pd.DataFrame(rows), snapshot)

            exit_code = health_main([
                str(snapshot),
                "--expected-git-sha",
                GIT_SHA,
                "--json-output",
                str(json_path),
                "--markdown-output",
                str(markdown_path),
            ])
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            markdown = markdown_path.read_text(encoding="utf-8")

        self.assertEqual(0, exit_code)
        self.assertEqual("ok", payload["status"])
        self.assertIn("# Daily snapshot health", markdown)
        self.assertIn("PreGap coverage", markdown)

    def test_markdown_lists_warning_tickers(self):
        report = {
            "status": "warning",
            "usable_for_shadow": True,
            "errors": [],
            "warnings": [{
                "code": "top10_pregap_unavailable",
                "message": "missing",
                "tickers": ["XEL"],
            }],
            "metrics": {},
        }

        markdown = render_health_markdown(report)

        self.assertIn("⚠️ WARNING", markdown)
        self.assertIn("XEL", markdown)


if __name__ == "__main__":
    unittest.main()
