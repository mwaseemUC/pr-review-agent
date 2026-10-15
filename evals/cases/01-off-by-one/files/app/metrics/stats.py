from __future__ import annotations

from collections.abc import Sequence


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean() of empty sequence")
    return sum(values) / len(values)


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        raise ValueError("percentile() of empty sequence")
    if not 0 <= pct <= 100:
        raise ValueError("pct must be between 0 and 100")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * pct / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def rolling_mean(series: Sequence[float], window: int) -> list[float]:
    """Mean of every consecutive `window`-sized slice of `series`, oldest first."""
    if window <= 0:
        raise ValueError("window must be positive")
    if window > len(series):
        return []
    return [mean(series[i : i + window]) for i in range(len(series) - window)]


def summarize_latencies(samples_ms: Sequence[float], window: int = 5) -> dict[str, object]:
    if not samples_ms:
        return {"count": 0, "mean": None, "p95": None, "rolling": []}
    return {
        "count": len(samples_ms),
        "mean": round(mean(samples_ms), 2),
        "p95": round(percentile(samples_ms, 95), 2),
        "rolling": [round(value, 2) for value in rolling_mean(samples_ms, window)],
    }
