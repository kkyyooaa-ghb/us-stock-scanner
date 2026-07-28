"""Eligibility calculations for the scanner's immutable universe cohort."""
from __future__ import annotations

import math

import pandas as pd


def complete_session_dollar_volume(
    close: pd.Series,
    volume: pd.Series,
    *,
    lookback_days: int,
    statistic: str,
) -> float | None:
    """Return a dollar-volume statistic over the latest complete sessions.

    The close series defines the scanner's signal-bar calendar.  Every one of
    its latest ``lookback_days`` dates must have a finite, positive close and
    volume observation; pandas' normal NaN-skipping aggregation is deliberately
    not allowed to turn a partial window into an eligible sample.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if statistic != "median":
        raise ValueError(f"unsupported dollar-volume statistic:{statistic}")

    recent_close = pd.to_numeric(close, errors="coerce").tail(lookback_days)
    if len(recent_close) != lookback_days:
        return None

    recent_volume = pd.to_numeric(volume, errors="coerce").reindex(
        recent_close.index
    )
    if recent_close.isna().any() or recent_volume.isna().any():
        return None
    if not (recent_close.gt(0).all() and recent_volume.gt(0).all()):
        return None

    dollar_volume = recent_close * recent_volume
    if not all(math.isfinite(float(value)) for value in dollar_volume):
        return None

    result = float(dollar_volume.median())
    return result if math.isfinite(result) else None
