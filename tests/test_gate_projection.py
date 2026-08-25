"""V1.3 閘門達標預估測試。

重點不在「預估準不準」（樣本太少,本來就不準),而在:
  1. 分不清楚就不要猜 —— 資料不足、無到達、超出視界一律明說。
  2. cohort 首日的 backlog fold 不得混進到達率。
  3. 預估永遠不能改變調參授權。
"""
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gate_projection import project_gate_completion, _MAX_TRADING_DAYS


def _fake_resolver(as_of: str, trading_days: int) -> str:
    """把交易日當日曆日直接加,測試不依賴 exchange_calendars。"""
    return (pd.Timestamp(as_of) + pd.Timedelta(days=trading_days)).strftime("%Y-%m-%d")


def _episodes(rows):
    return pd.DataFrame(rows)


def _row(start, status, *, bars=1, exit_days=40):
    return {
        "EpisodeStartDate": start,
        "EpisodeStatus": status,
        "PlanTimeExitDays": exit_days,
        "V13BarsObserved": bars,
    }


def _cohort(day_counts, *, status="open", bars=1):
    """依 {日期: 筆數} 造 episodes。"""
    rows = []
    for day, count in day_counts.items():
        rows.extend(_row(day, status, bars=bars) for _ in range(count))
    return _episodes(rows)


class GuardsAgainstGuessing(unittest.TestCase):
    def test_empty_frame(self):
        result = project_gate_completion(pd.DataFrame())
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "no_episodes")

    def test_missing_columns(self):
        frame = pd.DataFrame([{"EpisodeStatus": "open"}])
        result = project_gate_completion(frame)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "missing_columns")

    def test_single_scan_day_is_insufficient(self):
        """只有 cohort 首日 = 只有 backlog,沒有任何日常到達率資訊。"""
        frame = _cohort({"2026-07-28": 25})
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "insufficient_history")

    def test_two_scan_days_still_insufficient(self):
        """丟掉首日後只剩 1 天,不足以估到達率。"""
        frame = _cohort({"2026-07-28": 25, "2026-07-29": 4})
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"], "insufficient_history")

    def test_three_scan_days_is_enough(self):
        frame = _cohort({"2026-07-28": 25, "2026-07-29": 4, "2026-07-30": 3})
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        self.assertTrue(result["ok"])
        self.assertEqual(result["basis"]["observed_scan_days"], 2)


class BacklogFoldIsExcluded(unittest.TestCase):
    def test_first_cohort_day_never_enters_arrival_rate(self):
        """首日 25 筆是一次性折疊;混進去會把到達率高估數倍。"""
        frame = _cohort({
            "2026-07-28": 25,
            "2026-07-29": 4,
            "2026-07-30": 4,
            "2026-07-31": 4,
        })
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        basis = result["basis"]
        self.assertEqual(basis["backlog_episodes_excluded"], 25)
        self.assertEqual(basis["observed_scan_days"], 3)
        self.assertAlmostEqual(basis["arrivals_per_scan_day"], 4.0)

    def test_rate_would_be_far_higher_if_backlog_counted(self):
        frame = _cohort({
            "2026-07-28": 25,
            "2026-07-29": 4,
            "2026-07-30": 4,
            "2026-07-31": 4,
        })
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        naive_rate = 37 / 4
        self.assertLess(result["basis"]["arrivals_per_scan_day"], naive_rate / 2)

    def test_normal_zero_arrival_scan_day_is_in_denominator(self):
        frame = _cohort({
            "2026-07-28": 25,
            "2026-07-29": 4,
            "2026-07-31": 4,
        })
        result = project_gate_completion(
            frame,
            observed_scan_dates=[
                "2026-07-28",
                "2026-07-29",
                "2026-07-30",
                "2026-07-31",
            ],
            session_resolver=_fake_resolver,
        )

        self.assertTrue(result["ok"])
        self.assertEqual(3, result["basis"]["observed_scan_days"])
        self.assertEqual(1, result["basis"]["zero_arrival_scan_days"])
        self.assertAlmostEqual(2.667, result["basis"]["arrivals_per_scan_day"])


class PipelineIsDeterministic(unittest.TestCase):
    def test_open_episodes_carry_remaining_bars(self):
        frame = _cohort(
            {"2026-07-28": 5, "2026-07-29": 2, "2026-07-30": 2}, bars=4
        )
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        self.assertEqual(result["basis"]["pipeline_open_episodes"], 9)

    def test_already_completed_counts_immediately(self):
        rows = [_row("2026-07-28", "completed") for _ in range(60)]
        rows += [_row("2026-07-29", "open"), _row("2026-07-30", "open")]
        result = project_gate_completion(
            _episodes(rows), session_resolver=_fake_resolver
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["milestones"]["minimum"]["trading_days"], 0)
        self.assertEqual(result["milestones"]["minimum"]["remaining"], 0)

    def test_overdue_open_episode_does_not_go_negative(self):
        """BarsObserved 超過 time exit 時,剩餘天數不可為負或 0。"""
        frame = _cohort(
            {"2026-07-28": 3, "2026-07-29": 2, "2026-07-30": 2}, bars=99
        )
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        self.assertTrue(result["ok"])
        self.assertGreaterEqual(result["milestones"]["minimum"]["trading_days"], 1)


class HorizonAndOrdering(unittest.TestCase):
    def test_target_is_never_earlier_than_minimum(self):
        frame = _cohort({
            "2026-07-28": 25, "2026-07-29": 4, "2026-07-30": 3, "2026-07-31": 4,
        })
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        minimum = result["milestones"]["minimum"]["trading_days"]
        target = result["milestones"]["target"]["trading_days"]
        self.assertLessEqual(minimum, target)

    def test_optimistic_is_never_later_than_pessimistic(self):
        frame = _cohort({
            "2026-07-28": 25, "2026-07-29": 4, "2026-07-30": 3, "2026-07-31": 4,
        })
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        for key in ("minimum", "target"):
            m = result["milestones"][key]
            self.assertLessEqual(
                m["trading_days_optimistic"], m["trading_days_pessimistic"]
            )

    def test_unreachable_rate_reports_beyond_horizon(self):
        """全部未成交 → 成交率 0 → 永遠到不了,必須明說而非給假日期。"""
        rows = []
        for day, count in {
            "2026-07-28": 5, "2026-07-29": 1, "2026-07-30": 1, "2026-07-31": 1,
        }.items():
            rows.extend(_row(day, "unfilled") for _ in range(count))
        result = project_gate_completion(
            _episodes(rows), session_resolver=_fake_resolver
        )
        self.assertTrue(result["ok"])
        minimum = result["milestones"]["minimum"]
        self.assertTrue(minimum["beyond_horizon"])
        self.assertIsNone(minimum["eta_date"])
        self.assertIsNone(minimum["trading_days"])

    def test_horizon_cap_is_respected(self):
        frame = _cohort({
            "2026-07-28": 2, "2026-07-29": 1, "2026-07-30": 1, "2026-07-31": 1,
        })
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        for key in ("minimum", "target"):
            days = result["milestones"][key]["trading_days"]
            if days is not None:
                self.assertLessEqual(days, _MAX_TRADING_DAYS)


class CalendarSeam(unittest.TestCase):
    def test_resolver_failure_keeps_trading_days(self):
        """拿不到日曆時仍要回交易日數,只是沒有日期。"""
        frame = _cohort({
            "2026-07-28": 25, "2026-07-29": 4, "2026-07-30": 3, "2026-07-31": 4,
        })
        result = project_gate_completion(
            frame, session_resolver=lambda as_of, days: None
        )
        minimum = result["milestones"]["minimum"]
        self.assertIsNotNone(minimum["trading_days"])
        self.assertIsNone(minimum["eta_date"])

    def test_confidence_is_low_on_few_scan_days(self):
        frame = _cohort({
            "2026-07-28": 25, "2026-07-29": 4, "2026-07-30": 3, "2026-07-31": 4,
        })
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        self.assertEqual(result["confidence"], "low")


class ProjectionNeverGrantsTuning(unittest.TestCase):
    def test_projection_has_no_authorization_field(self):
        """預估不得帶任何看起來像授權的欄位。"""
        frame = _cohort({
            "2026-07-28": 25, "2026-07-29": 4, "2026-07-30": 3, "2026-07-31": 4,
        })
        result = project_gate_completion(frame, session_resolver=_fake_resolver)
        flat = str(result)
        self.assertNotIn("parameter_tuning_allowed", flat)
        self.assertNotIn("tuning_ready", flat)

    def test_projection_failure_does_not_block_the_gate(self):
        """episode_analysis 對預估例外必須降級,不得中斷整份 summary。"""
        from episode_analysis import _safe_projection

        broken = pd.DataFrame([{"EpisodeStatus": "open"}])
        out = _safe_projection(broken, minimum_completed=60, target_completed=100)
        self.assertFalse(out["ok"])
        self.assertIn("reason", out)


class WeeklyReportRendering(unittest.TestCase):
    def test_missing_projection_renders_explicit_note(self):
        from weekly_report import _v13_projection_lines

        self.assertTrue(any("無" in line for line in _v13_projection_lines(None)))

    def test_failed_projection_shows_reason(self):
        from weekly_report import _v13_projection_lines

        lines = _v13_projection_lines({"ok": False, "reason": "insufficient_history"})
        self.assertTrue(any("insufficient_history" in line for line in lines))

    def test_successful_projection_shows_date_and_range(self):
        from weekly_report import _v13_projection_lines

        lines = _v13_projection_lines({
            "ok": True,
            "confidence": "low",
            "basis": {
                "pipeline_open_episodes": 21,
                "completions_per_scan_day": 2.6,
            },
            "milestones": {
                "minimum": {
                    "threshold": 60,
                    "trading_days": 55,
                    "eta_date": "2026-10-19",
                    "eta_date_optimistic": "2026-10-09",
                    "eta_date_pessimistic": "2026-11-17",
                    "beyond_horizon": False,
                }
            },
        })
        blob = "\n".join(lines)
        self.assertIn("2026-10-19", blob)
        self.assertIn("2026-10-09", blob)
        self.assertIn("2026-11-17", blob)
        self.assertIn("ConfigHash", blob)


if __name__ == "__main__":
    unittest.main()
