"""Create V1.3 episode and maturity artifacts from shadow performance CSV."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import pandas as pd

from episode_analysis import build_episode_analysis


def _read_performance(path: str) -> pd.DataFrame:
    try:
        return pd.read_csv(path, encoding="utf-8-sig")
    except pd.errors.EmptyDataError:
        return pd.DataFrame()


def _read_scan_dates(path: str | None) -> list[str] | None:
    if not path:
        return None
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    dates = payload.get("dates") if isinstance(payload, dict) else payload
    if not isinstance(dates, list):
        raise ValueError("scan dates JSON must be a list or an object with dates")
    return [str(value) for value in dates]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Collapse V1.3 daily shadow signals into trade episodes."
    )
    parser.add_argument("performance", help="shadow_performance.csv")
    parser.add_argument(
        "--episodes-output",
        default="shadow_episodes.csv",
    )
    parser.add_argument(
        "--summary-output",
        default="shadow_episode_summary.json",
    )
    parser.add_argument(
        "--markdown-output",
        default="shadow_episode_summary.md",
    )
    parser.add_argument(
        "--scan-dates-json",
        help="Usable scan dates, including normal zero-new-episode days",
    )
    args = parser.parse_args(argv)

    if not Path(args.performance).is_file():
        print(f"ERROR 找不到 shadow performance:{args.performance}")
        return 1

    performance = _read_performance(args.performance)
    try:
        scan_dates = _read_scan_dates(args.scan_dates_json)
        analysis = build_episode_analysis(
            performance,
            observed_scan_dates=scan_dates,
        )
    except Exception as exc:
        print(f"ERROR episode 建構失敗:{type(exc).__name__}:{exc}")
        return 1

    analysis.episodes.to_csv(
        args.episodes_output,
        index=False,
        encoding="utf-8-sig",
    )
    Path(args.summary_output).write_text(
        json.dumps(analysis.summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    Path(args.markdown_output).write_text(
        analysis.markdown,
        encoding="utf-8",
    )

    maturity = analysis.summary["maturity"]
    print(
        f"OK Episodes {analysis.summary['episodes']} / "
        f"raw signals {analysis.summary['raw_signals']} | "
        f"completed R {maturity['completed_r']} | "
        f"gate {maturity['stage']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
