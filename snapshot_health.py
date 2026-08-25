"""Daily health assessment for the scanner's immutable snapshot artifact."""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
import os
from pathlib import Path
from typing import Iterable

import pandas as pd

from config import Config
from snapshot_schema import canonicalize_snapshot, snapshot_data_rows


GAP_ROUNDING_TOLERANCE_PCT = 0.011
HEALTH_POLICY_VERSION = "snapshot-usability-v1"
# Explicit migration boundary: 2026-08-21 is the last archived scan date that
# predates required sidecars. Any later missing sidecar is an incident, even if
# an older workflow happened to produce it before this branch was deployed.
HEALTH_SIDECAR_REQUIRED_FROM = "2026-08-22"
ELIGIBILITY_EXCLUSION_REASONS = frozenset({
    "insufficient_history",
    "price_below_min",
    "liquidity_below_min",
})
DATA_QUALITY_EXCLUSION_REASONS = frozenset({
    "stale_bar",
    "download_missing",
    "processing_error",
})
SYSTEMIC_DATA_QUALITY_RATIO = 0.05
SYSTEMIC_DATA_QUALITY_MIN_COUNT = 2


def _counts(series: pd.Series) -> dict[str, int]:
    return {
        str(key): int(value)
        for key, value in series.astype(str).value_counts(dropna=False).items()
    }


def _unique_text(frame: pd.DataFrame, column: str) -> list[str]:
    if column not in frame.columns:
        return []
    return sorted(frame[column].dropna().astype(str).unique().tolist())


def _finding(
    code: str,
    message: str,
    *,
    tickers: Iterable[str] = (),
) -> dict:
    normalized = sorted({str(ticker) for ticker in tickers if str(ticker)})
    result = {"code": code, "message": message}
    if normalized:
        result["tickers"] = normalized
        result["count"] = len(normalized)
    return result


def _blocked_report(code: str, message: str) -> dict:
    return {
        "policy_version": HEALTH_POLICY_VERSION,
        "status": "blocked",
        "usable_for_shadow": False,
        "eligible_for_weekly_calibration": False,
        "retryable": False,
        "errors": [_finding(code, message)],
        "warnings": [],
        "metrics": {},
    }


def evaluate_snapshot_health(
    frame: pd.DataFrame | None,
    *,
    expected_git_sha: str = "",
    expected_tickers: Iterable[str] | None = None,
) -> dict:
    """Validate one current snapshot and return a JSON-safe health report.

    Blocking findings mean the artifact must not enter shadow analysis.
    Warnings preserve a usable artifact while making partial source quality
    visible to operators.
    """
    try:
        snapshot = canonicalize_snapshot(frame)
    except Exception as exc:
        return _blocked_report("schema_invalid", str(exc))

    if snapshot.empty:
        return _blocked_report("snapshot_empty", "snapshot has no rows")

    record_type = snapshot["SnapshotRecordType"].astype(str)
    run_status = snapshot["SnapshotRunStatus"].astype(str)
    control = snapshot[record_type.eq("control")].copy()
    if not control.empty:
        metrics = {
            "record_types": _counts(record_type),
            "run_status": _counts(run_status),
            "control_types": _counts(control["SnapshotErrorType"]),
        }
        if len(control) == len(snapshot) and run_status.eq("skipped").all():
            return {
                "policy_version": HEALTH_POLICY_VERSION,
                "status": "skipped",
                "usable_for_shadow": False,
                "eligible_for_weekly_calibration": False,
                "retryable": False,
                "errors": [],
                "warnings": [],
                "metrics": metrics,
            }
        return {
            "policy_version": HEALTH_POLICY_VERSION,
            "status": "blocked",
            "usable_for_shadow": False,
            "eligible_for_weekly_calibration": False,
            "retryable": False,
            "errors": [
                _finding(
                    "control_snapshot",
                    "scanner produced an error or empty control snapshot",
                )
            ],
            "warnings": [],
            "metrics": metrics,
        }

    data = snapshot_data_rows(snapshot)
    audit = snapshot[record_type.eq("universe_audit")].copy()
    universe = snapshot[record_type.isin({"data", "universe_audit"})].copy()
    errors: list[dict] = []
    warnings: list[dict] = []

    expected = tuple(
        Config.SCAN_POOL if expected_tickers is None else expected_tickers
    )
    expected_set = {str(ticker).strip() for ticker in expected}
    observed_tickers = universe["Ticker"].astype(str).str.strip()
    observed_set = set(observed_tickers)
    missing_tickers = expected_set - observed_set
    unexpected_tickers = observed_set - expected_set
    if missing_tickers or unexpected_tickers:
        details = []
        if missing_tickers:
            details.append(f"missing={len(missing_tickers)}")
        if unexpected_tickers:
            details.append(f"unexpected={len(unexpected_tickers)}")
        errors.append(
            _finding(
                "universe_membership_mismatch",
                "universe ticker set mismatch: " + ", ".join(details),
                tickers=missing_tickers | unexpected_tickers,
            )
        )

    declared_expected = pd.to_numeric(
        universe["UniverseExpectedCount"],
        errors="coerce",
    )
    if (
        declared_expected.isna().any()
        or not declared_expected.eq(len(expected_set)).all()
    ):
        errors.append(
            _finding(
                "universe_expected_count_mismatch",
                f"UniverseExpectedCount must equal {len(expected_set)}",
            )
        )

    snapshot_times = _unique_text(universe, "SnapshotAsOfET")
    if len(snapshot_times) != 1:
        errors.append(
            _finding(
                "snapshot_time_inconsistent",
                "universe rows must share one sealed SnapshotAsOfET",
            )
        )

    expected_sha = str(expected_git_sha).strip()
    git_shas = _unique_text(universe, "GitCommitSha")
    if expected_sha and git_shas != [expected_sha]:
        errors.append(
            _finding(
                "git_sha_mismatch",
                f"snapshot GitCommitSha does not equal {expected_sha}",
            )
        )

    reasons = (
        _counts(audit["UniverseExclusionReason"])
        if not audit.empty
        else {}
    )
    if reasons:
        warnings.append(
            _finding(
                "universe_not_processed",
                "some universe tickers were excluded or missing: "
                + ", ".join(f"{key}={value}" for key, value in reasons.items()),
                tickers=audit["Ticker"].astype(str),
            )
        )

    data_quality_count = sum(
        reasons.get(reason, 0)
        for reason in DATA_QUALITY_EXCLUSION_REASONS
    )
    eligibility_count = sum(
        reasons.get(reason, 0)
        for reason in ELIGIBILITY_EXCLUSION_REASONS
    )
    systemic_threshold = max(
        SYSTEMIC_DATA_QUALITY_MIN_COUNT,
        math.ceil(len(expected_set) * SYSTEMIC_DATA_QUALITY_RATIO),
    )
    systemic_data_quality = data_quality_count >= systemic_threshold
    if systemic_data_quality:
        errors.append(
            _finding(
                "systemic_data_quality_failure",
                "data-quality exclusions reached the systemic threshold: "
                f"{data_quality_count}/{len(expected_set)} >= "
                f"{systemic_threshold}",
                tickers=audit.loc[
                    audit["UniverseExclusionReason"].astype(str).isin(
                        DATA_QUALITY_EXCLUSION_REASONS
                    ),
                    "Ticker",
                ],
            )
        )

    insufficient_history_count = reasons.get("insufficient_history", 0)
    systemic_insufficient_history = (
        insufficient_history_count >= systemic_threshold
    )
    if systemic_insufficient_history:
        errors.append(
            _finding(
                "systemic_insufficient_history",
                "insufficient-history exclusions reached the systemic threshold: "
                f"{insufficient_history_count}/{len(expected_set)} >= "
                f"{systemic_threshold}",
                tickers=audit.loc[
                    audit["UniverseExclusionReason"].astype(str).eq(
                        "insufficient_history"
                    ),
                    "Ticker",
                ],
            )
        )

    gap_status = data["PreGapStatus"].astype(str)
    available_mask = gap_status.eq("available")
    unavailable = data.loc[~available_mask, "Ticker"].astype(str).tolist()
    if unavailable:
        warnings.append(
            _finding(
                "pregap_unavailable",
                "some processed tickers do not have a trusted premarket gap",
                tickers=unavailable,
            )
        )

    available = data.loc[available_mask].copy()
    if not available.empty:
        gap = pd.to_numeric(available["PreGapPct"], errors="coerce")
        premarket = pd.to_numeric(
            available["PreMarketPrice"],
            errors="coerce",
        )
        reference = pd.to_numeric(
            available["PreGapReferencePrice"],
            errors="coerce",
        )
        invalid_price_mask = (
            gap.isna()
            | premarket.isna()
            | reference.isna()
            | premarket.le(0)
            | reference.le(0)
        )
        if invalid_price_mask.any():
            errors.append(
                _finding(
                    "pregap_prices_invalid",
                    "available PreGap rows require positive numeric prices",
                    tickers=available.loc[invalid_price_mask, "Ticker"],
                )
            )
        valid_price_mask = ~invalid_price_mask
        recomputed = (
            premarket[valid_price_mask] / reference[valid_price_mask] - 1.0
        ) * 100.0
        mismatch = (
            recomputed - gap[valid_price_mask]
        ).abs().gt(GAP_ROUNDING_TOLERANCE_PCT)
        if mismatch.any():
            errors.append(
                _finding(
                    "pregap_formula_mismatch",
                    "stored PreGapPct cannot be reproduced from stored prices",
                    tickers=available.loc[
                        recomputed.index[mismatch],
                        "Ticker",
                    ],
                )
            )
        bad_reference_date = available[
            available["PreGapReferenceDate"].astype(str).ne(
                available["DataBarDate"].astype(str)
            )
        ]
        if not bad_reference_date.empty:
            errors.append(
                _finding(
                    "pregap_reference_date_mismatch",
                    "PreGap reference date must equal the signal-bar date",
                    tickers=bad_reference_date["Ticker"],
                )
            )

    priority = pd.to_numeric(data["Priority"], errors="coerce")
    eligible = data[priority.ge(Config.MIN_PRIORITY_FOR_GO)]
    top = eligible.head(Config.TOP_N_RECOMMENDED)
    top_gap_unavailable = top[
        ~top["PreGapStatus"].astype(str).eq("available")
    ]
    if not top_gap_unavailable.empty:
        warnings.append(
            _finding(
                "top10_pregap_unavailable",
                "Top 10 contains tickers without a trusted premarket gap",
                tickers=top_gap_unavailable["Ticker"],
            )
        )

    plan_status = _counts(data["TradePlanStatus"])
    ready = data[data["TradePlanStatus"].astype(str).eq("shadow_ready")]
    available_count = int(available_mask.sum())
    data_count = int(len(data))
    coverage = available_count / data_count if data_count else 0.0

    metrics = {
        "identity": {
            "snapshot_schema_versions": _unique_text(
                universe, "SnapshotSchemaVersion"
            ),
            "signal_engine_versions": _unique_text(
                universe, "SignalEngineVersion"
            ),
            "trade_plan_versions": _unique_text(
                universe, "TradePlanVersion"
            ),
            "plan_measurement_versions": _unique_text(
                universe, "PlanMeasurementVersion"
            ),
            "config_hashes": _unique_text(universe, "ConfigHash"),
            "git_commit_shas": git_shas,
            "snapshot_as_of_et": snapshot_times,
        },
        "universe": {
            "expected": len(expected_set),
            "processed": data_count,
            "excluded": int(
                audit["UniverseDisposition"].astype(str).eq("excluded").sum()
            ),
            "missing": int(
                audit["UniverseDisposition"].astype(str).eq("missing").sum()
            ),
            "reasons": reasons,
        },
        "usability_policy": {
            "version": HEALTH_POLICY_VERSION,
            "eligibility_reasons": sorted(ELIGIBILITY_EXCLUSION_REASONS),
            "data_quality_reasons": sorted(DATA_QUALITY_EXCLUSION_REASONS),
            "eligibility_exclusions": eligibility_count,
            "data_quality_exclusions": data_quality_count,
            "systemic_threshold": systemic_threshold,
            "systemic_ratio": SYSTEMIC_DATA_QUALITY_RATIO,
            "systemic_data_quality": systemic_data_quality,
            "systemic_insufficient_history": systemic_insufficient_history,
        },
        "data_bar_dates": _counts(data["DataBarDate"]),
        "pregap": {
            "statuses": _counts(gap_status),
            "available": available_count,
            "coverage_rate": round(coverage, 6),
        },
        "trade_plans": {
            "statuses": plan_status,
            "shadow_ready": int(len(ready)),
            "selected_legs": _counts(data["SelectedLeg"]),
            "ready_order_types": (
                _counts(ready["OrderType"]) if not ready.empty else {}
            ),
        },
        "selection": {
            "eligible": int(len(eligible)),
            "top10": top["Ticker"].astype(str).tolist(),
            "top10_pregap_unavailable": (
                top_gap_unavailable["Ticker"].astype(str).tolist()
            ),
        },
    }

    status = "blocked" if errors else ("warning" if warnings else "ok")
    usable = not errors
    retryable_codes = {
        "systemic_data_quality_failure",
        "systemic_insufficient_history",
    }
    retryable = bool(errors) and all(
        finding.get("code") in retryable_codes
        for finding in errors
    )
    return {
        "policy_version": HEALTH_POLICY_VERSION,
        "status": status,
        "usable_for_shadow": usable,
        "eligible_for_weekly_calibration": usable,
        "retryable": retryable,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
    }


def render_health_markdown(report: dict) -> str:
    """Render a compact GitHub Step Summary from a health report."""
    status = str(report.get("status", "blocked")).upper()
    icon = {
        "OK": "✅",
        "WARNING": "⚠️",
        "SKIPPED": "⏭️",
        "BLOCKED": "🛑",
    }.get(status, "🛑")
    metrics = report.get("metrics", {})
    universe = metrics.get("universe", {})
    pregap = metrics.get("pregap", {})
    plans = metrics.get("trade_plans", {})
    selection = metrics.get("selection", {})
    lines = [
        "# Daily snapshot health",
        "",
        f"## {icon} {status}",
        "",
    ]
    if universe:
        lines.extend([
            "| Metric | Value |",
            "|---|---:|",
            f"| Expected universe | {universe.get('expected', 0)} |",
            f"| Processed | {universe.get('processed', 0)} |",
            f"| Excluded | {universe.get('excluded', 0)} |",
            f"| Missing | {universe.get('missing', 0)} |",
            f"| PreGap coverage | {pregap.get('available', 0)}/"
            f"{universe.get('processed', 0)} "
            f"({pregap.get('coverage_rate', 0):.1%}) |",
            f"| Shadow-ready plans | {plans.get('shadow_ready', 0)} |",
            f"| Eligible signals | {selection.get('eligible', 0)} |",
            "",
        ])
    for title, key in (("Errors", "errors"), ("Warnings", "warnings")):
        findings = report.get(key, [])
        if findings:
            lines.append(f"### {title}")
            lines.append("")
            for finding in findings:
                suffix = ""
                if finding.get("tickers"):
                    suffix = " — " + ", ".join(finding["tickers"])
                lines.append(
                    f"- `{finding.get('code', 'unknown')}`: "
                    f"{finding.get('message', '')}{suffix}"
                )
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_health_report(report: dict, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def health_report_allows_calibration(report: dict | None) -> bool:
    if not isinstance(report, dict):
        return False
    return bool(
        report.get(
            "eligible_for_weekly_calibration",
            report.get("usable_for_shadow", False),
        )
    )


def legacy_health_fallback_allowed(snapshot_date: str) -> bool:
    """Allow missing sidecars only before the health contract became required."""
    try:
        observed = date.fromisoformat(str(snapshot_date).strip()[:10])
        required_from = date.fromisoformat(HEALTH_SIDECAR_REQUIRED_FROM)
    except ValueError:
        return False
    return observed < required_from


def read_health_report(path: str | Path) -> dict:
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        return _blocked_report("health_report_invalid", str(exc))
    if not isinstance(report, dict):
        return _blocked_report("health_report_invalid", "health report must be an object")
    return report


def archived_snapshot_eligibility(
    snapshot_path: str | Path,
    *,
    health_dir: str | Path | None = None,
) -> dict:
    """Resolve a daily archive's sidecar without rewriting legacy history.

    Missing sidecars are accepted only as explicit legacy provenance. All newly
    archived snapshots receive a sidecar, so a present blocked sidecar always
    quarantines the raw CSV while leaving that CSV untouched.
    """
    snapshot = Path(snapshot_path)
    directory = (
        Path(health_dir)
        if health_dir is not None
        else snapshot.parent.parent / "daily_health"
    )
    sidecar = directory / f"{snapshot.stem}.json"
    if not sidecar.is_file():
        legacy = legacy_health_fallback_allowed(snapshot.stem)
        if not legacy:
            report = _blocked_report(
                "health_report_missing",
                "health sidecar is required from "
                f"{HEALTH_SIDECAR_REQUIRED_FROM}: {sidecar}",
            )
            return {
                "eligible": False,
                "legacy_without_health": False,
                "snapshot": str(snapshot),
                "health_path": str(sidecar),
                "health": report,
            }
        return {
            "eligible": True,
            "legacy_without_health": True,
            "snapshot": str(snapshot),
            "health_path": str(sidecar),
        }
    report = read_health_report(sidecar)
    return {
        "eligible": health_report_allows_calibration(report),
        "legacy_without_health": False,
        "snapshot": str(snapshot),
        "health_path": str(sidecar),
        "health": report,
    }


def _append_markdown(report: dict, path: str | Path) -> None:
    destination = Path(path)
    with destination.open("a", encoding="utf-8") as handle:
        handle.write(render_health_markdown(report))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate and summarize one scanner snapshot.",
    )
    parser.add_argument("snapshot")
    parser.add_argument(
        "--expected-git-sha",
        default=os.environ.get("GITHUB_SHA", ""),
    )
    parser.add_argument("--json-output")
    parser.add_argument("--markdown-output")
    parser.add_argument(
        "--github-output",
        default=os.environ.get("GITHUB_OUTPUT", ""),
    )
    args = parser.parse_args(argv)

    try:
        frame = pd.read_csv(args.snapshot, encoding="utf-8-sig")
        report = evaluate_snapshot_health(
            frame,
            expected_git_sha=args.expected_git_sha,
        )
    except Exception as exc:
        report = _blocked_report("snapshot_read_failed", str(exc))

    if args.json_output:
        write_health_report(report, args.json_output)
    if args.markdown_output:
        _append_markdown(report, args.markdown_output)
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8") as handle:
            handle.write(f"status={report.get('status', 'blocked')}\n")
            handle.write(
                "usable="
                + str(bool(report.get("usable_for_shadow"))).lower()
                + "\n"
            )
            handle.write(
                "retryable="
                + str(bool(report.get("retryable"))).lower()
                + "\n"
            )

    universe = report.get("metrics", {}).get("universe", {})
    print(
        "snapshot health "
        f"{str(report['status']).upper()}: "
        f"processed={universe.get('processed', 0)} "
        f"excluded={universe.get('excluded', 0)} "
        f"missing={universe.get('missing', 0)}"
    )
    for finding in report.get("errors", []) + report.get("warnings", []):
        print(f"  {finding['code']}: {finding['message']}")
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())
