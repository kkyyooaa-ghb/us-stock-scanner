import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime_provenance import collect_runtime_provenance, main


class RuntimeProvenanceTests(unittest.TestCase):
    def test_runtime_identity_is_explicitly_separate_from_strategy_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            requirements = Path(tmp) / "requirements.txt"
            requirements.write_text("pandas>=2.0\n", encoding="utf-8")
            with patch(
                "runtime_provenance.resolved_distributions",
                return_value=[{"name": "pandas", "version": "2.3.1"}],
            ):
                report = collect_runtime_provenance(requirements)

        self.assertEqual("runtime_only_not_strategy_config_hash", report["identity_scope"])
        self.assertIn("github_sha", report["workflow"])
        self.assertIn("runner_os", report["workflow"])
        self.assertEqual(
            [{"name": "pandas", "version": "2.3.1"}],
            report["resolved_distributions"],
        )
        self.assertNotIn("ConfigHash", report)

    def test_cli_writes_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            requirements = root / "requirements.txt"
            output = root / "runtime.json"
            requirements.write_text("numpy>=2.0\n", encoding="utf-8")
            with patch(
                "runtime_provenance.resolved_distributions",
                return_value=[],
            ):
                exit_code = main([
                    "--output", str(output),
                    "--requirements", str(requirements),
                ])
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(0, exit_code)
        self.assertEqual(1, payload["schema_version"])
        self.assertEqual(64, len(payload["requirements"]["sha256"]))


if __name__ == "__main__":
    unittest.main()
