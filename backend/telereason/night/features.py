"""Gate features (M0): eight summaries per setting-B channel, as in the 11 Oct gate.

For every channel in the pool's setting-B vocabulary the gate sees the window's mean, standard
deviation, minimum, maximum, first value, last value, slope per row and a presence flag.  The
values are the rows that carry the channel; a window without the channel gets eight zeros.
The matrix is float32, like on 11 Oct.
"""

from __future__ import annotations

import numpy as np

from telereason.night.windows import Pool, Window

STATS = ("mean", "std", "min", "max", "first", "last", "slope", "present")


def feature_names(channels: list[str]) -> list[str]:
    """``<channel>.<summary>`` for every column of the matrix."""
    return [f"{channel}.{stat}" for channel in channels for stat in STATS]


def _slope(values: np.ndarray) -> float:
    """Least-squares slope against the row number (a straight-line fit)."""
    if len(values) < 2:
        return 0.0
    x = np.arange(len(values), dtype="float64")
    x -= x.mean()
    return float((x * (values - values.mean())).sum() / (x * x).sum())


def window_features(window: Window, channels: list[str]) -> np.ndarray:
    columns = {name: i for i, name in enumerate(window.channels)}
    out = np.zeros((len(channels), len(STATS)), dtype="float64")
    for c, name in enumerate(channels):
        if name not in columns:
            continue
        values = window.values[:, columns[name]]
        values = values[~np.isnan(values)]
        if not len(values):
            continue
        out[c] = (
            values.mean(),
            values.std(),
            values.min(),
            values.max(),
            values[0],
            values[-1],
            _slope(values),
            1.0,
        )
    return out.reshape(-1)


def gate_features(pool: Pool) -> np.ndarray:
    """One row per pool window, ``len(pool.channels) * 8`` columns, float32."""
    width = len(pool.channels) * len(STATS)
    rows = [window_features(window, pool.channels) for window in pool.windows]
    return np.array(rows, dtype="float32").reshape(len(rows), width)
