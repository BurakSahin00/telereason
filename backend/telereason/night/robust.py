"""Robust z for the facts: z = (x - median) / max(1.4826 * MAD, floor).

The 11 Oct facts divided by the normal windows' standard deviation, which is tiny for signals
that barely move in normal operation, so small differences read as several standard
deviations.  The median and MAD resist single outliers in the reference, and the floor keeps a
near-constant reference from inflating z.

The floor is the standard deviation of the feature over every pool window on the same line,
every label included (the 11 Oct z experiment, ``p29_normal.py``, used 0.1 times the training
windows' standard deviation).  With 0.1 the reference MAD is so small that 96% of normal windows
read "Clearly off normal"; with 1.0 the median of a normal window's largest |z| is 2.7-3.0,
close to the 2.7 of the largest |z| among 106 independent standard-normal signals, so the
plain-reading thresholds keep their meaning.  A signal whose spread is still 0 is constant:
its z is 0 and it never enters the facts.
"""

from __future__ import annotations

import warnings

import numpy as np

MAD_SCALE = 1.4826
FLOOR_SHARE = 1.0


def nan_std(values: np.ndarray) -> np.ndarray:
    """Population standard deviation per column, ignoring NaN; NaN for an empty column."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanstd(values, axis=0)


def floor(line_values: np.ndarray) -> np.ndarray:
    """Per-column floor from every window on the line; 0 where the column has no values."""
    return np.nan_to_num(FLOOR_SHARE * nan_std(line_values), nan=0.0)


def median_mad(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Median and scaled MAD (1.4826 * MAD) per column, ignoring NaN."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        centre = np.nanmedian(values, axis=0)
        mad = np.nanmedian(np.abs(values - centre), axis=0)
    return centre, MAD_SCALE * mad


def median_scale(values: np.ndarray, column_floor: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(median, max(1.4826 * MAD, floor)) per column."""
    centre, spread = median_mad(values)
    return centre, np.fmax(spread, column_floor)


def robust_z(x: np.ndarray, centre: np.ndarray, spread: np.ndarray) -> np.ndarray:
    """(x - centre) / spread, and 0 where the spread is 0 or the inputs are missing."""
    usable = np.isfinite(x) & np.isfinite(centre) & np.isfinite(spread) & (spread > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(usable, (x - centre) / np.where(usable, spread, 1.0), 0.0)
