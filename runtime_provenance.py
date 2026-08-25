"""Record resolved runtime dependencies without changing strategy identity."""
from __future__ import annotations

import argparse
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import sys


PROVENANCE_SCHEMA_VERSION = 1


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolved_distributions() -> list[dict[str, str]]:
    """Return a deterministic, credential-free package inventory."""
    packages: dict[str, str] = {}
    for dist in metadata.distributions():
        name = str(dist.metadata.get("Name") or "").strip()
        if name:
            packages[name.lower()] = str(dist.version)
    return [
        {"name": name, "version": packages[name]}
        for name in sorted(packages)
    ]


def collect_runtime_provenance(
    requirements_path: str | Path = "requirements.txt",
) -> dict:
    requirements = Path(requirements_path)
    return {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "identity_scope": "runtime_only_not_strategy_config_hash",
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable_version_info": list(sys.version_info[:3]),
        },
        "platform": platform.platform(),
        "workflow": {
            "github_sha": os.environ.get("GITHUB_SHA", ""),
            "github_run_id": os.environ.get("GITHUB_RUN_ID", ""),
            "github_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT", ""),
            "runner_os": os.environ.get("RUNNER_OS", ""),
            "runner_arch": os.environ.get("RUNNER_ARCH", ""),
            "image_os": os.environ.get("ImageOS", ""),
        },
        "requirements": {
            "path": requirements.as_posix(),
            "sha256": _sha256(requirements),
        },
        "resolved_distributions": resolved_distributions(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write resolved runtime dependency provenance."
    )
    parser.add_argument("--output", default="runtime_provenance.json")
    parser.add_argument("--requirements", default="requirements.txt")
    args = parser.parse_args(argv)

    report = collect_runtime_provenance(args.requirements)
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"runtime provenance {args.output}: "
        f"{len(report['resolved_distributions'])} distributions"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
