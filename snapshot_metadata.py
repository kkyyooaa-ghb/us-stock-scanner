"""Pure helpers for finalizing immutable scanner snapshot metadata."""
from __future__ import annotations

import pandas as pd


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
