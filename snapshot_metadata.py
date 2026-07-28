"""Pure helpers for finalizing immutable scanner snapshot metadata."""
from __future__ import annotations

import pandas as pd


def latest_complete_bar_levels(
    high: pd.Series,
    low: pd.Series,
) -> tuple[float, float]:
    """Return the signal bar's high and low after partial bars are removed."""
    if high is None or high.empty or low is None or low.empty:
        raise ValueError("complete signal-bar high/low required")
    return float(high.iloc[-1]), float(low.iloc[-1])


def finalize_snapshot_timing(
    frame: pd.DataFrame,
    *,
    timing: dict,
    snapshot_as_of_et: str,
) -> pd.DataFrame:
    """Rebase every plan to the timing known when the whole snapshot is ready."""
    if frame is None:
        return pd.DataFrame()
    if frame.empty:
        return frame.copy()

    out = frame.copy()
    out["SnapshotAsOfET"] = snapshot_as_of_et
    out["ScanSession"] = timing["scan_session"]
    out["ScanAfterOpen"] = int(bool(timing["scan_after_open"]))
    out["SnapshotTimingSource"] = timing["source"]

    selected = out["SelectedLeg"].astype(str).ne("none")
    if timing["ok"]:
        out["PlanEarliestEntryDate"] = timing["earliest_entry_date"]
        out.loc[selected, "TradePlanStatus"] = "shadow_ready"
        if "PlanReason" in out:
            out.loc[selected, "PlanReason"] = (
                out.loc[selected, "PlanReason"]
                .astype(str)
                .str.replace(
                    ";entry_calendar_unavailable",
                    "",
                    regex=False,
                )
            )
    else:
        out["PlanEarliestEntryDate"] = None
        out.loc[selected, "TradePlanStatus"] = "timing_unavailable"
        if "PlanReason" in out:
            reason = out.loc[selected, "PlanReason"].astype(str)
            suffix = ";entry_calendar_unavailable"
            out.loc[selected, "PlanReason"] = reason.where(
                reason.str.contains(
                    "entry_calendar_unavailable",
                    regex=False,
                ),
                reason + suffix,
            )
    return out


def demote_stale_bar_plans(
    frame: pd.DataFrame,
    *,
    expected_bar_date: str | None,
) -> tuple[pd.DataFrame, int]:
    """Keep stale-bar rows in the snapshot but bar them from shadow measurement.

    A row whose signal bar is not the expected last complete session describes a
    different day than every other row, so its plan must never accumulate R.
    Scores, legs and Top-10 membership are deliberately left untouched — full
    ranking exclusion is a selection change and belongs to the next engine
    version, not to this measurement-only slice.
    """
    if frame is None or frame.empty or "DataBarDate" not in frame:
        return (pd.DataFrame() if frame is None else frame.copy()), 0

    out = frame.copy()
    if not expected_bar_date:
        stale = pd.Series(True, index=out.index)
    else:
        stale = out["DataBarDate"].astype(str).ne(str(expected_bar_date))
    demote = stale & out["TradePlanStatus"].astype(str).isin(
        {"shadow_ready", "timing_unavailable"}
    )
    out.loc[demote, "TradePlanStatus"] = "data_stale"
    if "PlanReason" in out and demote.any():
        reason = out.loc[demote, "PlanReason"].astype(str)
        suffix = ";signal_bar_not_last_complete_session"
        out.loc[demote, "PlanReason"] = reason.where(
            reason.str.contains(suffix.lstrip(";"), regex=False),
            reason + suffix,
        )
    return out, int(demote.sum())


def finalize_theme_metadata(
    frame: pd.DataFrame,
    *,
    min_priority: int,
    top_n: int,
) -> pd.DataFrame:
    """Finalize theme score decomposition and selection counterfactuals.

    The returned frame preserves production ordering by post-theme ``Score``.
    ``EnteredTop10DueToTheme`` compares the actual Top N with the Top N that
    would have been selected if ThemeScore were removed.
    """
    if frame.empty:
        return frame.copy()

    out = frame.sort_values("Score", ascending=False).copy()
    out["PriorityPostTheme"] = out["Priority"]
    out["RankScore"] = out["Score"]
    out["EligiblePostTheme"] = (
        out["PriorityPostTheme"] >= min_priority
    ).astype(int)
    out["CrossedThresholdDueToTheme"] = (
        (out["PriorityPreTheme"] < min_priority)
        & (out["PriorityPostTheme"] >= min_priority)
    ).astype(int)
    out["EnteredTop10DueToTheme"] = 0

    counterfactual_top = set(
        out[out["PriorityPreTheme"] >= min_priority]
        .sort_values("ScorePreTheme", ascending=False)
        .head(top_n)
        .index
    )
    actual_top = set(
        out[out["PriorityPostTheme"] >= min_priority]
        .head(top_n)
        .index
    )
    for index in actual_top - counterfactual_top:
        out.at[index, "EnteredTop10DueToTheme"] = 1

    return out
