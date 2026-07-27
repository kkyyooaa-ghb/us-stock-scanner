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
    args = parser.parse_args(argv)

    if not Path(args.performance).is_file():
        print(f"ERROR 找不到 shadow performance:{args.performance}")
        return 1

    performance = _read_performance(args.performance)
    try:
        analysis = build_episode_analysis(performance)
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
