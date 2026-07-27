import json
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from build_shadow_episodes import main


class EpisodeCliTests(unittest.TestCase):
    def test_writes_csv_json_and_markdown_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "shadow_performance.csv"
            episodes = root / "episodes.csv"
            summary = root / "summary.json"
            markdown = root / "summary.md"
            pd.DataFrame([{
                "SnapshotAsOfET": "2026-01-05T09:00:00-05:00",
                "Ticker": "TEST",
                "TradePlanVersion": "v1.3.0-shadow",
                "PlanSelectedLeg": "consolidation_dip",
                "OrderType": "buy_limit_zone",
                "V13MeasurementStatus": "completed",
                "V13Filled": 1,
                "V13Ambiguous": 0,
                "V13RLower": 0.5,
                "V13RUpper": 0.5,
                "V13LifecycleEndDate": "2026-01-06",
            }]).to_csv(source, index=False, encoding="utf-8-sig")

            code = main([
                str(source),
                "--episodes-output", str(episodes),
                "--summary-output", str(summary),
                "--markdown-output", str(markdown),
            ])

            self.assertEqual(0, code)
            self.assertEqual(1, len(pd.read_csv(episodes)))
            payload = json.loads(summary.read_text(encoding="utf-8"))
            self.assertEqual(1, payload["episodes"])
            self.assertIn(
                "V1.3 Shadow Episode Report",
                markdown.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
