import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from config import Config
from snapshot_schema import (
    SNAPSHOT_COLUMNS,
    canonicalize_snapshot,
    ensure_snapshot,
    snapshot_data_rows,
    write_failure_snapshot,
    write_skipped_snapshot,
)


def _base_data_row(**overrides):
    row = {
        "SnapshotRecordType": "data",
        "UniverseExpectedCount": 1,
        "UniverseDisposition": "processed",
        "UniverseExclusionReason": "",
        "DollarVolumeMedian20": 500_000_000.0,
        "Ticker": "TEST",
        "Priority": 7,
        "Score": 7.5,
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
    }
    row.update(overrides)
    return row


class SnapshotSchemaTests(unittest.TestCase):
    def test_normal_rows_are_written_with_one_canonical_schema(self):
        source = pd.DataFrame([_base_data_row()])

        result = canonicalize_snapshot(source)

        self.assertEqual(list(SNAPSHOT_COLUMNS), list(result.columns))
        self.assertEqual("data", result.iloc[0]["SnapshotRecordType"])
        self.assertEqual("ok", result.iloc[0]["SnapshotRunStatus"])
        self.assertEqual(
            Config.SNAPSHOT_SCHEMA_VERSION,
            result.iloc[0]["SnapshotSchemaVersion"],
        )
        self.assertIn("PreGapStatus", result.columns)
        self.assertIn("PlanEarliestEntryDate", result.columns)

    def test_unknown_columns_fail_instead_of_being_silently_dropped(self):
        with self.assertRaisesRegex(ValueError, "unknown snapshot columns"):
            canonicalize_snapshot(
                pd.DataFrame([{"Ticker": "TEST", "UndeclaredColumn": 1}])
            )

    def test_failure_snapshot_uses_the_same_schema_and_csv_escaping(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_result.csv"

            write_failure_snapshot(path, 'broken, with "quotes"')
            result = pd.read_csv(path, encoding="utf-8-sig")

        self.assertEqual(list(SNAPSHOT_COLUMNS), list(result.columns))
        self.assertEqual(1, len(result))
        self.assertEqual("control", result.iloc[0]["SnapshotRecordType"])
        self.assertEqual("error", result.iloc[0]["SnapshotRunStatus"])
        self.assertEqual(
            'broken, with "quotes"',
            result.iloc[0]["SnapshotErrorMessage"],
        )

    def test_empty_result_is_a_typed_control_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_result.csv"
            from snapshot_schema import write_snapshot

            write_snapshot(pd.DataFrame(), path)
            result = pd.read_csv(path, encoding="utf-8-sig")

        self.assertEqual(list(SNAPSHOT_COLUMNS), list(result.columns))
        self.assertEqual("control", result.iloc[0]["SnapshotRecordType"])
        self.assertEqual("empty", result.iloc[0]["SnapshotRunStatus"])

    def test_intentional_non_session_skip_is_not_labeled_as_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_result.csv"

            write_skipped_snapshot(
                path,
                "2026-07-26:weekend",
                skip_type="NonTradingSession",
            )
            result = pd.read_csv(path, encoding="utf-8-sig")

        self.assertEqual("control", result.iloc[0]["SnapshotRecordType"])
        self.assertEqual("skipped", result.iloc[0]["SnapshotRunStatus"])
        self.assertEqual(
            "NonTradingSession",
            result.iloc[0]["SnapshotErrorType"],
        )

    def test_pregap_and_ready_plan_invariants_are_enforced(self):
        invalid_gap = pd.DataFrame([_base_data_row(
            PreGapPct=1.2,
            PreGapStatus="fetch_error",
        )])
        with self.assertRaisesRegex(ValueError, "PreGapPct requires available"):
            canonicalize_snapshot(invalid_gap)

        invalid_plan = invalid_gap.assign(
            PreGapPct=pd.NA,
            PreGapStatus="disabled",
            TradePlanStatus="shadow_ready",
            SelectedLeg="oversold_bounce",
            PlanSelectedLeg="oversold_bounce",
            LegAnchor="previous_high",
            PlanAnchor="previous_high",
            LegAnchorPrice=101,
            PlanAnchorPrice=102,
            OrderType="buy_stop_reclaim",
            TriggerPrice=102,
            PlanEntryLow=102,
            PlanEntryHigh=103,
            PlanStopLoss=90,
            PlanValidDays=5,
            PlanTimeExitDays=40,
            PlanStopType="intraday",
            PlanExitRule="initial_stop_or_d40_close",
            TradePlanVersion=Config.TRADE_PLAN_VERSION,
            PlanMeasurementVersion=Config.SHADOW_MEASUREMENT_VERSION,
        )
        with self.assertRaisesRegex(ValueError, "anchor price mismatch"):
            canonicalize_snapshot(invalid_plan)

        valid_plan = invalid_plan.assign(PlanAnchorPrice=101)
        result = canonicalize_snapshot(valid_plan)
        self.assertEqual("shadow_ready", result.iloc[0]["TradePlanStatus"])

    def test_invalid_dates_and_incomplete_executable_plans_fail_closed(self):
        bad_date = pd.DataFrame([_base_data_row(
            DataBarDate="2026-02-30",
        )])
        with self.assertRaisesRegex(ValueError, "invalid DataBarDate"):
            canonicalize_snapshot(bad_date)

        incomplete = pd.DataFrame([_base_data_row(
            TradePlanStatus="shadow_ready",
            SelectedLeg="oversold_bounce",
            PlanSelectedLeg="oversold_bounce",
            LegAnchor="previous_high",
            PlanAnchor="previous_high",
            LegAnchorPrice=101,
            PlanAnchorPrice=101,
            OrderType="buy_stop_reclaim",
            TriggerPrice=pd.NA,
            PlanEntryLow=102,
            PlanEntryHigh=103,
            PlanStopLoss=90,
            PlanValidDays=5,
            PlanTimeExitDays=40,
            PlanStopType="intraday",
            PlanExitRule="initial_stop_or_d40_close",
        )])
        with self.assertRaisesRegex(ValueError, "executable numeric"):
            canonicalize_snapshot(incomplete)

        executable_none = pd.DataFrame([_base_data_row(
            TradePlanStatus="shadow_ready",
            SelectedLeg="none",
            PlanSelectedLeg="none",
            LegAnchor="none",
            PlanAnchor="none",
            LegAnchorPrice=101,
            PlanAnchorPrice=101,
            OrderType="buy_stop_reclaim",
            TriggerPrice=102,
            PlanEntryLow=102,
            PlanEntryHigh=103,
            PlanStopLoss=90,
            PlanValidDays=5,
            PlanTimeExitDays=40,
            PlanStopType="intraday",
            PlanExitRule="initial_stop_or_d40_close",
        )])
        with self.assertRaisesRegex(ValueError, "executable selected leg"):
            canonicalize_snapshot(executable_none)

    def test_envelope_record_type_and_status_are_validated(self):
        bad_status = pd.DataFrame([_base_data_row(
            SnapshotRunStatus="error",
        )])
        with self.assertRaisesRegex(ValueError, "data rows require ok"):
            canonicalize_snapshot(bad_status)

        invalid_score = pd.DataFrame([_base_data_row(Score=float("inf"))])
        with self.assertRaisesRegex(ValueError, "finite numeric"):
            canonicalize_snapshot(invalid_score)

        missing_priority = pd.DataFrame([_base_data_row()])
        missing_priority = missing_priority.drop(columns=["Priority"])
        with self.assertRaisesRegex(ValueError, "missing required snapshot"):
            canonicalize_snapshot(missing_priority)

    def test_ensure_only_creates_a_missing_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_result.csv"
            self.assertTrue(ensure_snapshot(path, error="missing_output"))
            original = path.read_bytes()
            self.assertFalse(ensure_snapshot(path, error="must_not_replace"))
            self.assertEqual(original, path.read_bytes())

    def test_atomic_failure_preserves_existing_snapshot_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scan_result.csv"
            path.write_bytes(b"original")

            with patch(
                "snapshot_schema.pd.DataFrame.to_csv",
                side_effect=RuntimeError("interrupted write"),
            ):
                with self.assertRaisesRegex(RuntimeError, "interrupted"):
                    write_failure_snapshot(path, "failure")

            self.assertEqual(b"original", path.read_bytes())
            self.assertEqual([], list(path.parent.glob(".scan_result.csv.*.tmp")))

    def test_data_reader_filters_canonical_controls_and_legacy_errors(self):
        canonical = pd.DataFrame([
            {"SnapshotRecordType": "control", "Ticker": pd.NA},
            {"SnapshotRecordType": "data", "Ticker": "AAPL"},
        ])
        legacy = pd.DataFrame([
            {"Ticker": "ERROR"},
            {"Ticker": "MSFT"},
        ])

        self.assertEqual(
            ["AAPL"],
            snapshot_data_rows(canonical)["Ticker"].tolist(),
        )
        self.assertEqual(
            ["MSFT"],
            snapshot_data_rows(legacy)["Ticker"].tolist(),
        )


if __name__ == "__main__":
    unittest.main()
