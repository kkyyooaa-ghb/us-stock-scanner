"""V1.3 調參分析工具。

閘門在 2026-10 前後才會開。這支工具現在就建好,目的有二:
  1. 閘門一開就能立刻跑,不必那時才開始寫。
  2. 現在就能用手上的樣本驗證工具本身 —— 用 legacy 或未達標樣本**測試工具**
     從來都是允許的,那和用它們**調參**是兩回事。

## 為什麼不是「分組算平均 R 就好」

策略調參最典型的死法,是把十幾個維度都切一遍、挑出最好看的那格,然後宣稱
找到 edge。切 15 格、每格用 95% 信賴水準,純噪音也有約 54% 機率至少冒出一
格「顯著」。本模組因此把結論分成兩層,且兩層都印出來:

  - **探索層(未校正)**:各格自己的 95% 區間。用來產生假說,**不能**當結論。
  - **確認層(Bonferroni 校正)**:以格數校正後的區間仍排除 0 才算數。

多數情況下確認層會是空的。那不是工具壞了,那就是答案 —— 每格 20 筆撐不起
可靠結論。模組因此一併算出「要多少筆才解析得動觀察到的效果」,讓「再等」
成為有依據的決定,而不是拖延。

## 統計方法

R 的分布不是常態:停損在 -1 附近有質量點,獲利側是連續尾巴。因此用 bootstrap
百分位區間,不用 t 分布。

同日雙觸的 episode 保存 R 上下界(日線無法判斷盤中順序)。本模組對上下界
**各算一次**,只有兩邊結論一致才標為 robust —— 不用單一數字掩蓋那個已知的
不確定性。

## 授權

本模組**不授權任何事**。閘門未開時輸出照樣產生,但每份輸出都標記
`authorized=False`,且 `findings` 一律清空。授權只由
`weekly_report.load_v13_calibration_gate` 判定。
"""
from __future__ import annotations

import argparse
import json
from typing import Any

import numpy as np
import pandas as pd

from config import Config
from episode_analysis import (
    TUNING_SCOPE_ORDER_TYPES,
    TUNING_SCOPE_SELECTED_LEGS,
)

BOOTSTRAP_ITERATIONS = 10_000
DEFAULT_ALPHA = 0.05
_RNG_SEED = 20260806  # 固定種子:同一份資料必須每次給出同一個答案


def _bootstrap_mean_ci(
    values: np.ndarray,
    alpha: float,
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int = _RNG_SEED,
) -> tuple[float, float]:
    """平均值的 bootstrap 百分位區間。

    樣本少於 2 筆時無從估計離散度,回 (nan, nan) 而不是假裝有區間。
    """
    n = len(values)
    if n < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    draws = rng.choice(values, size=(iterations, n), replace=True)
    means = draws.mean(axis=1)
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return (lo, hi)


def _required_n(values: np.ndarray, target_half_width: float = 0.25) -> int | None:
    """要多少樣本才能把 95% 區間的半寬壓到 target_half_width 以內。

    用常態近似 1.96*sd/sqrt(n) 反解 —— 這裡只求數量級,不需要精確。
    回傳 None 表示樣本太少,連 sd 都估不動。
    """
    if len(values) < 2:
        return None
    sd = float(np.std(values, ddof=1))
    if sd == 0:
        return len(values)
    return int(np.ceil((1.96 * sd / target_half_width) ** 2))


def derive_dimensions(episodes: pd.DataFrame) -> pd.DataFrame:
    """加上分析用的分層欄位。原欄位一律不動。"""
    out = episodes.copy()

    if "VixLevel" in out.columns:
        vix = pd.to_numeric(out["VixLevel"], errors="coerce")
        out["VixBucket"] = pd.cut(
            vix,
            bins=[-np.inf, 15, 20, 25, np.inf],
            labels=["vix<15", "vix15-20", "vix20-25", "vix>=25"],
        ).astype(object)

    if "PriorityPostTheme" in out.columns:
        priority = pd.to_numeric(out["PriorityPostTheme"], errors="coerce")
        out["PriorityBucket"] = pd.cut(
            priority,
            bins=[-np.inf, 7, 9, 11, np.inf],
            labels=["P7", "P8-9", "P10-11", "P12+"],
        ).astype(object)

    # 主題加分是否實際改變了這筆的命運(而不是只有分數變動)
    for column, label in (
        ("CrossedThresholdDueToTheme", "theme_crossed_threshold"),
        ("EnteredTop10DueToTheme", "theme_entered_top10"),
    ):
        if column in out.columns:
            out[label] = out[column].map(
                lambda v: "yes" if str(v).strip().lower() in {"true", "1", "yes"}
                else "no"
            )
    return out


DEFAULT_DIMENSIONS = (
    "PlanSelectedLeg",
    "OrderType",
    "CandidateLeg",
    "MarketBias",
    "VixBucket",
    "PriorityBucket",
    "theme_crossed_threshold",
    "theme_entered_top10",
    "PreGapStatus",
)

FORMAL_SEGMENT_SCOPES = {
    "PlanSelectedLeg": frozenset(TUNING_SCOPE_SELECTED_LEGS),
    "OrderType": frozenset(TUNING_SCOPE_ORDER_TYPES),
}


def _in_tuning_scope(dimension: str, level: str) -> bool:
    """Whether a categorical level belongs to the formal decision scope.

    Unrecognised selected legs and order types remain visible as descriptive
    diagnostics, but can never become eligible hypotheses or tuning findings.
    Other exploratory dimensions are not constrained by this formal segment
    catalogue.
    """
    allowed = FORMAL_SEGMENT_SCOPES.get(dimension)
    return allowed is None or level in allowed


def _completed(episodes: pd.DataFrame) -> pd.DataFrame:
    status = episodes.get("EpisodeStatus")
    if status is None:
        status = episodes.get("V13MeasurementStatus")
    if status is None:
        return episodes.iloc[0:0]
    return episodes.loc[status.astype(str).eq("completed")].copy()


def _cell_stats(
    frame: pd.DataFrame,
    r_column: str,
    alpha: float,
    adjusted_alpha: float,
    min_cell: int,
) -> dict[str, Any] | None:
    values = pd.to_numeric(frame[r_column], errors="coerce").dropna().to_numpy()
    n = len(values)
    if n == 0:
        return None

    mean_r = float(values.mean())
    lo, hi = _bootstrap_mean_ci(values, alpha)
    adj_lo, adj_hi = _bootstrap_mean_ci(values, adjusted_alpha)
    eligible = n >= min_cell

    def excludes_zero(low: float, high: float) -> bool:
        if np.isnan(low) or np.isnan(high):
            return False
        return low > 0 or high < 0

    return {
        "n": n,
        "mean_r": round(mean_r, 4),
        "ci_low": None if np.isnan(lo) else round(lo, 4),
        "ci_high": None if np.isnan(hi) else round(hi, 4),
        "adj_ci_low": None if np.isnan(adj_lo) else round(adj_lo, 4),
        "adj_ci_high": None if np.isnan(adj_hi) else round(adj_hi, 4),
        "win_rate": round(float((values > 0).mean()), 4),
        "stop_rate": round(float((values <= -1).mean()), 4),
        "eligible": eligible,
        "exploratory_signal": eligible and excludes_zero(lo, hi),
        "confirmed_signal": eligible and excludes_zero(adj_lo, adj_hi),
        "required_n_for_quarter_r": _required_n(values),
    }


def analyze_tuning(
    episodes: pd.DataFrame,
    *,
    gate_allows_tuning: bool,
    dimensions: tuple[str, ...] = DEFAULT_DIMENSIONS,
    min_cell: int = Config.EPISODE_SEGMENT_MIN_COMPLETED,
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    """分層統計 completed-R,並區分探索與確認兩層結論。

    `gate_allows_tuning` 由呼叫端從 V1.3 gate 取得。為 False 時照樣輸出統計
    (工具驗證用),但 `findings` 清空、`authorized` 為 False。
    """
    enriched = derive_dimensions(episodes)
    completed = _completed(enriched)

    present = [d for d in dimensions if d in enriched.columns]
    missing = [d for d in dimensions if d not in enriched.columns]

    # 先數一次要檢定幾格,才能決定校正幅度。只有 eligible 的格子算進檢定數 ——
    # 樣本不足的格子本來就不會下結論,不該稀釋校正。
    cell_index: list[tuple[str, str, pd.DataFrame]] = []
    for dimension in present:
        for level, group in completed.groupby(
            completed[dimension].astype(str), dropna=False
        ):
            cell_index.append((dimension, str(level), group))
    eligible_cells = sum(
        1
        for dimension, level, group in cell_index
        if len(group) >= min_cell and _in_tuning_scope(dimension, level)
    )
    hypotheses = max(eligible_cells, 1)
    adjusted_alpha = alpha / hypotheses

    results: dict[str, Any] = {}
    for r_column, bound in (("V13RLower", "lower"), ("V13RUpper", "upper")):
        if r_column not in completed.columns:
            continue
        per_dimension: dict[str, Any] = {}
        for dimension in present:
            cells = {}
            for dim, level, group in cell_index:
                if dim != dimension:
                    continue
                stats = _cell_stats(
                    group, r_column, alpha, adjusted_alpha, min_cell
                )
                if stats is not None:
                    in_tuning_scope = _in_tuning_scope(dimension, level)
                    stats["in_tuning_scope"] = in_tuning_scope
                    if not in_tuning_scope:
                        stats["eligible"] = False
                        stats["exploratory_signal"] = False
                        stats["confirmed_signal"] = False
                    cells[level] = stats
            if cells:
                per_dimension[dimension] = cells
        results[bound] = per_dimension

    # robust = R 上下界都得到同一個確認結論。同日雙觸的不確定性不可被平均掉。
    findings = []
    lower = results.get("lower", {})
    upper = results.get("upper", {})
    for dimension, cells in lower.items():
        for level, stats in cells.items():
            other = (upper.get(dimension) or {}).get(level)
            if not stats["in_tuning_scope"] or not stats["confirmed_signal"]:
                continue
            robust = bool(other and other["confirmed_signal"])
            findings.append({
                "dimension": dimension,
                "level": level,
                "n": stats["n"],
                "mean_r_lower_bound": stats["mean_r"],
                "mean_r_upper_bound": other["mean_r"] if other else None,
                "robust_across_r_bounds": robust,
            })

    return {
        "authorized": bool(gate_allows_tuning),
        "completed_episodes": len(completed),
        "min_cell": min_cell,
        "alpha": alpha,
        "hypotheses_tested": eligible_cells,
        "adjusted_alpha": round(adjusted_alpha, 6),
        "dimensions_analysed": present,
        "dimensions_missing": missing,
        "by_bound": results,
        # 閘門未開時不得輸出任何結論,即使統計上算得出來
        "findings": findings if gate_allows_tuning else [],
        "findings_withheld": (not gate_allows_tuning) and bool(findings),
    }


def render_markdown(analysis: dict[str, Any]) -> str:
    lines = ["# V1.3 調參分析", ""]

    if not analysis["authorized"]:
        lines.extend([
            "> ⛔ **未授權 —— 本輸出僅供工具驗證,不得據以調參。**",
            "> V1.3 閘門尚未開放。以下統計照常計算是為了驗證工具本身,",
            "> 任何結論一律扣住不發。",
            "",
        ])
        if analysis["findings_withheld"]:
            lines.extend([
                "> ℹ️ 本次統計上確實出現通過校正的格子,已依規定**不列出**。",
                "",
            ])

    lines.extend([
        f"- 已完成 R episodes：{analysis['completed_episodes']}",
        f"- 每格最低樣本：{analysis['min_cell']}",
        f"- 檢定格數：{analysis['hypotheses_tested']}"
        f"（α {analysis['alpha']} → 校正後 {analysis['adjusted_alpha']}）",
    ])
    if analysis["dimensions_missing"]:
        lines.append(
            f"- 缺欄未分析：{', '.join(analysis['dimensions_missing'])}"
        )
    lines.append("")

    if analysis["hypotheses_tested"] == 0:
        lines.extend([
            "## 尚無任何格達到最低樣本",
            "",
            "所有分層的 completed-R 都不足,無法下任何結論。",
            "",
        ])

    for bound in ("lower", "upper"):
        per_dimension = analysis["by_bound"].get(bound)
        if not per_dimension:
            continue
        lines.extend([f"## R {bound} bound", ""])
        for dimension, cells in per_dimension.items():
            lines.extend([
                f"### {dimension}",
                "",
                "| level | n | mean R | 95% CI | 校正後 CI | 勝率 | 停損率 | "
                "正式範圍 | 達標 | 探索 | 確認 | 需要 n |",
                "|---|---:|---:|---|---|---:|---:|:---:|:---:|:---:|:---:|---:|",
            ])
            for level, s in cells.items():
                def fmt(low, high):
                    if low is None or high is None:
                        return "-"
                    return f"{low:+.2f}~{high:+.2f}"

                lines.append(
                    f"| {level} | {s['n']} | {s['mean_r']:+.2f} | "
                    f"{fmt(s['ci_low'], s['ci_high'])} | "
                    f"{fmt(s['adj_ci_low'], s['adj_ci_high'])} | "
                    f"{s['win_rate']:.0%} | {s['stop_rate']:.0%} | "
                    f"{'yes' if s['in_tuning_scope'] else 'no'} | "
                    f"{'yes' if s['eligible'] else 'no'} | "
                    f"{'yes' if s['exploratory_signal'] else '-'} | "
                    f"{'yes' if s['confirmed_signal'] else '-'} | "
                    f"{s['required_n_for_quarter_r'] or '-'} |"
                )
            lines.append("")

    if analysis["authorized"]:
        lines.extend(["## 結論", ""])
        if not analysis["findings"]:
            lines.extend([
                "校正後沒有任何分層的 R 平均顯著偏離 0。",
                "",
                "這是有效結論,不是失敗 —— 代表目前樣本不支持任何調參動作。",
                "上表「需要 n」欄顯示各格要多少樣本才解析得動觀察到的效果。",
                "",
            ])
        else:
            lines.extend([
                "| 維度 | level | n | R(下界) | R(上界) | 兩界一致 |",
                "|---|---|---:|---:|---:|:---:|",
            ])
            for f in analysis["findings"]:
                upper_r = f["mean_r_upper_bound"]
                lines.append(
                    f"| {f['dimension']} | {f['level']} | {f['n']} | "
                    f"{f['mean_r_lower_bound']:+.2f} | "
                    f"{upper_r:+.2f} | "
                    f"{'yes' if f['robust_across_r_bounds'] else '⚠️ no'} |"
                )
            lines.extend([
                "",
                "> 兩界不一致者代表結論取決於同日雙觸的盤中順序假設,"
                "日線無法判定 —— 不可據以調參。",
                "",
            ])

    lines.append(
        "> 探索層未校正多重比較,只能產生假說;確認層才是結論。"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="V1.3 調參分析")
    parser.add_argument(
        "episodes", nargs="?", default="reports/shadow_episodes.csv"
    )
    parser.add_argument(
        "--summary", default="reports/shadow_episode_summary.json",
        help="用於讀取閘門授權狀態",
    )
    parser.add_argument("--json-output")
    parser.add_argument("--markdown-output")
    parser.add_argument(
        "--force-authorized", action="store_true",
        help="僅供測試:略過閘門檢查(不會用於正式流程)",
    )
    args = parser.parse_args(argv)

    episodes = pd.read_csv(args.episodes)

    allowed = args.force_authorized
    if not allowed:
        from weekly_report import load_v13_calibration_gate

        gate = load_v13_calibration_gate(args.summary)
        allowed = bool(
            gate.get("ok")
            and gate.get(
                "analysis_review_allowed",
                gate.get("parameter_tuning_allowed"),
            )
        )

    analysis = analyze_tuning(episodes, gate_allows_tuning=allowed)
    markdown = render_markdown(analysis)

    if args.json_output:
        with open(args.json_output, "w", encoding="utf-8") as handle:
            json.dump(analysis, handle, ensure_ascii=False, indent=2)
    if args.markdown_output:
        with open(args.markdown_output, "w", encoding="utf-8") as handle:
            handle.write(markdown)
    if not args.json_output and not args.markdown_output:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
