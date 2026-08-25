import unittest

import pandas as pd

from config import Config
from episode_analysis import build_episode_analysis
from trade_plan import strategy_config_hash, universe_version


def _row(
    date,
    ticker,
    status,
    *,
    lifecycle_end=None,
    leg="consolidation_dip",
    order_type="buy_limit_zone",
    filled=False,
    ambiguous=False,
    r_lower=None,
    r_upper=None,
):
    return {
        "SnapshotAsOfET": f"{date}T09:00:00-05:00",
        "Ticker": ticker,
        "TradePlanVersion": Config.TRADE_PLAN_VERSION,
        "PlanMeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "V13MeasurementVersion": Config.SHADOW_MEASUREMENT_VERSION,
        "SignalEngineVersion": Config.SIGNAL_ENGINE_VERSION,
        "ConfigHash": strategy_config_hash(),
        "UniverseVersion": universe_version(),
        "PlanSelectedLeg": leg,
        "SelectedLeg": leg,
        "OrderType": order_type,
        "V13MeasurementStatus": status,
        "V13Filled": int(filled),
        "V13Ambiguous": int(ambiguous),
        "V13RLower": r_lower,
        "V13RUpper": r_upper,
        "V13MFER": 1.5 if status == "completed" else None,
        "V13MAER": -0.5 if status == "completed" else None,
        "V13LifecycleEndDate": lifecycle_end,
        "V13EntryWindowEndDate": (
            lifecycle_end if status == "unfilled" else None
        ),
    }


class EpisodeGroupingTests(unittest.TestCase):
    def test_signals_inside_canonical_lifecycle_collapse_to_one_episode(self):
        performance = pd.DataFrame([
            _row(
                "2026-01-05",
                "AAA",
                "completed",
                lifecycle_end="2026-01-09",
                filled=True,
                r_lower=0.5,
                r_upper=0.5,
            ),
            _row(
                "2026-01-06",
                "AAA",
                "completed",
                lifecycle_end="2026-01-06",
                leg="oversold_bounce",
                order_type="buy_stop_reclaim",
                filled=True,
                r_lower=1.0,
                r_upper=1.0,
            ),
            _row(
                "2026-01-12",
                "AAA",
                "unfilled",
                lifecycle_end="2026-01-14",
            ),
        ])

        analysis = build_episode_analysis(performance)

        self.assertEqual(2, len(analysis.episodes))
        first = analysis.episodes.iloc[0]
        self.assertEqual(2, first["EpisodeSignalCount"])
        self.assertEqual(1, first["EpisodeDuplicateSignals"])
        self.assertEqual(
            "consolidation_dip|oversold_bounce",
            first["EpisodeObservedLegs"],
        )
        self.assertEqual("consolidation_dip", first["PlanSelectedLeg"])

    def test_unfilled_window_and_data_errors_end_the_episode(self):
        performance = pd.DataFrame([
            _row(
                "2026-01-05",
                "AAA",
                "unfilled",
                lifecycle_end="2026-01-09",
            ),
            _row(
                "2026-01-08",
                "AAA",
                "unfilled",
                lifecycle_end="2026-01-12",
            ),
            _row(
                "2026-01-12",
                "AAA",
                "unfilled",
                lifecycle_end="2026-01-14",
            ),
            _row("2026-01-05", "BBB", "no_data"),
            _row("2026-01-06", "BBB", "no_data"),
        ])

        analysis = build_episode_analysis(performance)

        aaa = analysis.episodes[analysis.episodes["Ticker"] == "AAA"]
        bbb = analysis.episodes[analysis.episodes["Ticker"] == "BBB"]
        self.assertEqual(2, len(aaa))
        self.assertEqual(2, aaa.iloc[0]["EpisodeSignalCount"])
        self.assertEqual(2, len(bbb))

    def test_open_position_suppresses_later_leg_changes(self):
        performance = pd.DataFrame([
            _row(
                "2026-01-05",
                "AAA",
                "open",
                filled=True,
            ),
            _row(
                "2026-02-02",
                "AAA",
                "completed",
                lifecycle_end="2026-02-02",
                leg="healthy_pullback",
                filled=True,
                r_lower=1,
                r_upper=1,
            ),
        ])

        analysis = build_episode_analysis(performance)

        self.assertEqual(1, len(analysis.episodes))
        self.assertEqual(2, analysis.episodes.iloc[0]["EpisodeSignalCount"])
        self.assertEqual(1, analysis.episodes.iloc[0]["EpisodeIsActive"])

    def test_episode_ids_are_deterministic(self):
        performance = pd.DataFrame([
            _row(
                "2026-01-05",
                "AAA",
                "completed",
                lifecycle_end="2026-01-06",
                filled=True,
                r_lower=1,
                r_upper=1,
            )
        ])

        first = build_episode_analysis(performance).episodes.iloc[0]["EpisodeId"]
        second = build_episode_analysis(performance).episodes.iloc[0]["EpisodeId"]

        self.assertEqual(first, second)
        self.assertRegex(first, r"^ep-[0-9a-f]{16}$")


class EpisodeKpiTests(unittest.TestCase):
    def test_empty_input_is_a_valid_collecting_report(self):
        analysis = build_episode_analysis(pd.DataFrame())

        self.assertTrue(analysis.episodes.empty)
        self.assertEqual(0, analysis.summary["episodes"])
        self.assertEqual("collecting", analysis.summary["maturity"]["stage"])
        self.assertEqual(
            Config.TRADE_PLAN_VERSION,
            analysis.summary["trade_plan_version"],
        )

    def test_reports_lifecycle_r_interval_and_segment_metrics(self):
        performance = pd.DataFrame([
            _row(
                "2026-01-05",
                "AAA",
                "completed",
                lifecycle_end="2026-01-06",
                filled=True,
                r_lower=1,
                r_upper=1,
            ),
            _row(
                "2026-01-05",
                "BBB",
                "completed",
                lifecycle_end="2026-01-06",
                leg="oversold_bounce",
                order_type="buy_stop_reclaim",
                filled=True,
                ambiguous=True,
                r_lower=-1,
                r_upper=1,
            ),
            _row(
                "2026-01-05",
                "CCC",
                "unfilled",
                lifecycle_end="2026-01-09",
            ),
            _row(
                "2026-01-05",
                "DDD",
                "open",
                leg="healthy_pullback",
                filled=True,
            ),
            _row("2026-01-05", "EEE", "awaiting_fill"),
        ])

        analysis = build_episode_analysis(performance)
        overall = analysis.summary["overall"]

        self.assertEqual(5, overall["episodes"])
        self.assertEqual(3, overall["filled"])
        self.assertEqual(1, overall["unfilled"])
        self.assertEqual(1, overall["open"])
        self.assertEqual(2, overall["completed_r"])
        self.assertEqual(1, overall["ambiguous"])
        self.assertEqual(0.75, overall["filled_rate"])
        self.assertEqual(0.25, overall["unfilled_rate"])
        self.assertEqual(0.0, overall["r_lower_mean"])
        self.assertEqual(1.0, overall["r_upper_mean"])
        self.assertEqual(0.5, overall["conservative_win_rate"])
        self.assertEqual(1.0, overall["optimistic_win_rate"])
        self.assertEqual("collecting", analysis.summary["maturity"]["stage"])
        self.assertEqual(2, len(analysis.summary["by_order_type"]))
        self.assertIn("By Selected Leg", analysis.markdown)

    def test_tuning_gate_requires_overall_and_segment_completion(self):
        performance = pd.DataFrame([
            _row(
                f"2026-01-{(index % 28) + 1:02d}",
                f"T{index:03d}",
                "completed",
                lifecycle_end=f"2026-01-{(index % 28) + 1:02d}",
                filled=True,
                r_lower=0.25,
                r_upper=0.25,
            )
            for index in range(60)
        ])

        analysis = build_episode_analysis(performance)

        self.assertTrue(
            analysis.summary["maturity"]["parameter_tuning_allowed"]
        )
        self.assertTrue(analysis.summary["maturity"]["global_analysis_allowed"])
        self.assertTrue(analysis.summary["maturity"]["analysis_review_allowed"])
        self.assertFalse(analysis.summary["maturity"]["power_ci_review_due"])
        self.assertFalse(
            analysis.summary["maturity"]["parameter_change_authorized"]
        )
        self.assertEqual(
            "overall_gate_with_independent_segments",
            analysis.summary["maturity"]["scope_model"],
        )
        self.assertFalse(
            analysis.summary["maturity"]["all_segments_required_for_global"]
        )
        self.assertFalse(
            analysis.summary["segment_scope"][
                "global_gate_requires_all_segments"
            ]
        )
        self.assertEqual("minimum_reached", analysis.summary["maturity"]["stage"])
        self.assertTrue(analysis.summary["by_selected_leg"][0]["tuning_ready"])

    def test_immature_segment_does_not_close_the_global_gate(self):
        performance = pd.DataFrame([
            _row(
                f"2026-01-{(index % 28) + 1:02d}",
                f"T{index:03d}",
                "completed",
                lifecycle_end=f"2026-01-{(index % 28) + 1:02d}",
                filled=True,
                r_lower=0.25,
                r_upper=0.25,
                leg=("consolidation_dip" if index < 50 else "oversold_bounce"),
            )
            for index in range(60)
        ])

        analysis = build_episode_analysis(performance)
        readiness = {
            row["segment"]: row["tuning_ready"]
            for row in analysis.summary["by_selected_leg"]
        }

        self.assertTrue(analysis.summary["maturity"]["global_analysis_allowed"])
        self.assertTrue(readiness["consolidation_dip"])
        self.assertFalse(readiness["oversold_bounce"])

    def test_unknown_segment_never_enters_tuning_scope(self):
        performance = pd.DataFrame([
            _row(
                f"2026-01-{(index % 28) + 1:02d}",
                f"T{index:03d}",
                "completed",
                lifecycle_end=f"2026-01-{(index % 28) + 1:02d}",
                filled=True,
                r_lower=0.25,
                r_upper=0.25,
                leg="future_unreviewed_leg",
            )
            for index in range(60)
        ])

        analysis = build_episode_analysis(performance)
        segment = analysis.summary["by_selected_leg"][0]

        self.assertTrue(analysis.summary["maturity"]["analysis_review_allowed"])
        self.assertFalse(segment["in_tuning_scope"])
        self.assertFalse(segment["tuning_ready"])

    def test_old_measurement_versions_cannot_unlock_tuning_gate(self):
        current = _row(
            "2026-01-05",
            "CURRENT",
            "completed",
            lifecycle_end="2026-01-05",
            filled=True,
            r_lower=0.25,
            r_upper=0.25,
        )
        old_rows = []
        for index in range(60):
            row = _row(
                f"2026-02-{(index % 28) + 1:02d}",
                f"OLD{index:03d}",
                "completed",
                lifecycle_end=f"2026-02-{(index % 28) + 1:02d}",
                filled=True,
                r_lower=1,
                r_upper=1,
            )
            row["TradePlanVersion"] = "v1.3.0-shadow"
            row["PlanMeasurementVersion"] = "v1.3.0-shadow"
            row["V13MeasurementVersion"] = "v1.3.0-shadow"
            old_rows.append(row)

        analysis = build_episode_analysis(pd.DataFrame([current, *old_rows]))

        self.assertEqual(61, analysis.summary["input_signals"])
        self.assertEqual(60, analysis.summary["excluded_version_signals"])
        self.assertEqual(1, analysis.summary["raw_signals"])
        self.assertEqual(1, analysis.summary["overall"]["completed_r"])
        self.assertFalse(
            analysis.summary["maturity"]["parameter_tuning_allowed"]
        )

    def test_missing_or_other_config_hash_cannot_enter_current_cohort(self):
        current = _row(
            "2026-01-05",
            "CURRENT",
            "completed",
            lifecycle_end="2026-01-05",
            filled=True,
            r_lower=0.25,
            r_upper=0.25,
        )
        other = dict(current)
        other.update(
            Ticker="OTHER",
            SnapshotAsOfET="2026-02-05T09:00:00-05:00",
            ConfigHash="different-selection-config",
        )

        mixed = build_episode_analysis(pd.DataFrame([current, other]))
        self.assertEqual(1, mixed.summary["raw_signals"])
        self.assertEqual(1, mixed.summary["excluded_version_signals"])
        self.assertEqual(
            {
                "signal_engine_version": Config.SIGNAL_ENGINE_VERSION,
                "config_hash": strategy_config_hash(),
            },
            mixed.summary["selection_cohort"],
        )

        missing_hash = build_episode_analysis(
            pd.DataFrame([current]).drop(columns=["ConfigHash"])
        )
        self.assertEqual(0, missing_hash.summary["raw_signals"])
        self.assertEqual(1, missing_hash.summary["excluded_version_signals"])
        self.assertFalse(
            missing_hash.summary["maturity"]["parameter_tuning_allowed"]
        )


if __name__ == "__main__":
    unittest.main()
