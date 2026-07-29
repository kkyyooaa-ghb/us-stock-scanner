import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class WorkflowContractTests(unittest.TestCase):
    def test_scan_workflow_runs_and_archives_snapshot_health(self):
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scan.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("python -m snapshot_health scan_result.csv", workflow)
        self.assertIn("--expected-git-sha \"$GITHUB_SHA\"", workflow)
        self.assertIn("--markdown-output \"$GITHUB_STEP_SUMMARY\"", workflow)
        self.assertIn("snapshot_health.json", workflow)

    def test_watchdog_checks_at_0915_and_0920_in_both_dst_seasons(self):
        workflow = (
            REPO_ROOT / ".github" / "workflows" / "scan_watchdog.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("cron: '15,20 13 * * 1-5'", workflow)
        self.assertIn("cron: '15,20 14 * * 1-5'", workflow)
        self.assertIn(
            'schedule == "15,20 13 * * 1-5" and offset == "-0400"',
            workflow,
        )
        self.assertIn(
            'schedule == "15,20 14 * * 1-5" and offset == "-0500"',
            workflow,
        )
        self.assertNotIn("15,25", workflow)


if __name__ == "__main__":
    unittest.main()
