"""Per-window facts: what the night note may say and what the window page shows.

Facts are computed by code; the model only puts them into words.  For every window:

- the three setting-B signals whose window mean deviates most from normal (largest |z|),
- the plan-versus-measured pair whose largest gap is most above normal (largest z),
- how many signals normal windows usually have but this window lacks,
- ``look_first``: the signal or pair with the largest |z| among those.

Normal is the windows labelled ``normal`` outside the window's own group: on FactoryWave the
same task on other days (all tasks on other days when that gives fewer than 10 windows), on
AURSAD other experiment blocks.  A channel needs 5 normal windows that carry it.  Values are
rounded to 3 significant digits; ``difference`` and ``excess_over_typical`` are computed from
the rounded values, so a note that copies them stays consistent.  This is the 11 Oct method
(``p35_gece.gece_girdileri`` and ``girdi_v2``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from telereason.api.schemas import Fact, Line
from telereason.night.channels import pair_name
from telereason.night.windows import Pool, Window

NORMAL = "normal"
MIN_REFERENCE = 5
MIN_SAME_TASK = 10
MIN_SPREAD = 1e-9
TOP_SIGNALS = 3
SCORE_NOTE = "screening model, 0 = normal-like, 1 = abnormal-like"

# (normal values, one column per channel, NaN where absent) -> (centre, spread) per column
Scale = Callable[[np.ndarray], tuple[np.ndarray, np.ndarray]]


def mean_std(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Mean and population standard deviation per column, ignoring NaN (the 11 Oct z)."""
    present = ~np.isnan(values)
    count = present.sum(axis=0)
    filled = np.where(present, values, 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        centre = filled.sum(axis=0) / count
        spread = np.sqrt(np.where(present, (values - centre) ** 2, 0.0).sum(axis=0) / count)
    return centre, spread


def y3(value: float | None) -> float | None:
    """3 significant digits; magnitudes below 0.001 become 0."""
    if value is None or not np.isfinite(value):
        return None
    return 0.0 if abs(float(value)) < 1e-3 else float(f"{float(value):.3g}")


def plain_reading(z: float) -> str:
    size = abs(z)
    if size < 2:
        reading = "Within the normal range"
    elif size < 3:
        reading = "Slightly off normal"
    else:
        reading = "Clearly off normal"
    return f"{reading} ({size:g} standard deviations)."


# ---------------------------------------------------------------- features


@dataclass(frozen=True)
class Features:
    """Window means per setting-B channel and largest plan-versus-measured gaps (NaN: absent)."""

    channels: list[str]
    pairs: list[tuple[str, str]]
    means: np.ndarray
    gaps: np.ndarray


def _window_features(
    window: Window, channels: list[str], pairs: list[tuple[str, str]]
) -> tuple[np.ndarray, np.ndarray]:
    columns = {name: i for i, name in enumerate(window.channels)}
    means = np.full(len(channels), np.nan)
    for c, name in enumerate(channels):
        if name in columns:
            column = window.values[:, columns[name]]
            column = column[~np.isnan(column)]
            if len(column):
                means[c] = column.mean()
    gaps = np.full(len(pairs), np.nan)
    for p, (planned, measured) in enumerate(pairs):
        if planned in columns and measured in columns:
            gap = window.values[:, columns[measured]] - window.values[:, columns[planned]]
            gap = gap[~np.isnan(gap)]
            if len(gap) >= 2:
                gaps[p] = np.abs(gap).max()
    return means, gaps


def features(pool: Pool) -> Features:
    rows = [_window_features(window, pool.channels, pool.pairs) for window in pool.windows]
    # 11 Oct kept features as float32; rounding through it reproduces its 3-digit facts exactly.
    means = np.array([row[0] for row in rows], dtype="float32").reshape(
        len(rows), len(pool.channels)
    )
    gaps = np.array([row[1] for row in rows], dtype="float32").reshape(len(rows), len(pool.pairs))
    return Features(
        channels=list(pool.channels),
        pairs=list(pool.pairs),
        means=means.astype("float64"),
        gaps=gaps.astype("float64"),
    )


# ---------------------------------------------------------------- reference


@dataclass(frozen=True)
class _Reference:
    description: str
    size: int
    signal_count: np.ndarray
    signal_centre: np.ndarray
    signal_spread: np.ndarray
    # Share of reference windows that carry each channel (for missing signals).
    signal_share: np.ndarray
    gap_count: np.ndarray
    gap_centre: np.ndarray
    gap_spread: np.ndarray


def _reference_masks(pool: Pool) -> tuple[list[tuple[str, ...]], dict[tuple[str, ...], Any]]:
    """Reference key per window, and (description, mask) per key."""
    windows = pool.windows
    line = np.array([w.line for w in windows])
    group = np.array([w.group for w in windows], dtype=object)
    task = np.array([w.task for w in windows], dtype=object)
    normal = np.array([w.label == NORMAL for w in windows])
    keys: list[tuple[str, ...]] = []
    masks: dict[tuple[str, ...], Any] = {}
    for window in windows:
        base = (line == window.line) & normal & (group != window.group)
        if window.line == "AURSAD":
            key: tuple[str, ...] = ("AURSAD", window.group)
            if key not in masks:
                masks[key] = ("other experiment blocks", base)
        else:
            key = ("FactoryWave", window.group, window.task)
            if key not in masks:
                same_task = base & (task == window.task)
                if same_task.sum() >= MIN_SAME_TASK:
                    masks[key] = ("same task, other days", same_task)
                else:
                    masks[key] = ("all tasks, other days", base)
        keys.append(key)
    return keys, masks


def _reference(description: str, mask: np.ndarray, feats: Features, scale: Scale) -> _Reference:
    means, gaps = feats.means[mask], feats.gaps[mask]
    signal_centre, signal_spread = scale(means)
    gap_centre, gap_spread = scale(gaps)
    size = int(mask.sum())
    signal_count = (~np.isnan(means)).sum(axis=0)
    return _Reference(
        description=description,
        size=size,
        signal_count=signal_count,
        signal_centre=signal_centre,
        signal_spread=signal_spread,
        signal_share=signal_count / size if size else np.zeros(len(feats.channels)),
        gap_count=(~np.isnan(gaps)).sum(axis=0),
        gap_centre=gap_centre,
        gap_spread=gap_spread,
    )


# ---------------------------------------------------------------- facts


@dataclass(frozen=True)
class WindowFacts:
    window_id: str
    ms_id: str
    line: Line
    task: str
    reference: str
    # signal, window_mean, normal_mean, z, difference (rounded)
    deviating: list[dict[str, Any]]
    # pair, max_abs_gap, normal_typical, z, excess_over_typical (rounded), or None
    gap: dict[str, Any] | None
    missing_signals: int
    look_first: str | None

    @property
    def max_abs_z(self) -> float | None:
        sizes = [abs(s["z"]) for s in self.deviating]
        if self.gap is not None:
            sizes.append(abs(self.gap["z"]))
        return max(sizes) if sizes else None

    def note_input(self, score: float | None = None) -> dict[str, Any]:
        """The note's input JSON, in the 11 Oct key order; ``score`` only on a screening line."""
        out: dict[str, Any] = {"window": self.window_id}
        if score is not None:
            out.update(abnormal_score=score, abnormal_score_note=SCORE_NOTE)
        out.update(
            line=self.line,
            task=self.task,
            reference=f"normal windows, {self.reference}",
            deviating_signals=[dict(s) for s in self.deviating],
            largest_plan_vs_measured_gap=dict(self.gap) if self.gap else None,
            missing_signals=self.missing_signals,
            look_first=self.look_first,
        )
        return out

    def fact_rows(self) -> list[Fact]:
        """``WindowDetail.facts``: deviating signals, plus the gap when it is the look-first one."""
        rows = [
            Fact(
                signal=s["signal"],
                window_mean=s["window_mean"],
                normal_mean=s["normal_mean"],
                difference=s["difference"],
                z=s["z"],
                plain=plain_reading(s["z"]),
            )
            for s in self.deviating
        ]
        if self.gap is not None and self.look_first == self.gap["pair"]:
            rows.append(
                Fact(
                    signal=self.gap["pair"],
                    window_mean=self.gap["max_abs_gap"],
                    normal_mean=self.gap["normal_typical"],
                    difference=self.gap["excess_over_typical"],
                    z=self.gap["z"],
                    plain=plain_reading(self.gap["z"]),
                )
            )
        return rows


def _difference(a: float | None, b: float | None) -> float | None:
    return y3(a - b) if a is not None and b is not None else None


def _window_facts(i: int, window: Window, ref: _Reference, feats: Features) -> WindowFacts:
    x = feats.means[i]
    present = ~np.isnan(x)
    usable = present & (ref.signal_count >= MIN_REFERENCE) & (ref.signal_spread > MIN_SPREAD)
    deviating = []
    if usable.any():
        z = np.where(usable, (x - ref.signal_centre) / np.where(usable, ref.signal_spread, 1), 0)
        candidates = np.flatnonzero(usable)
        order = candidates[np.argsort(-np.abs(z[candidates]), kind="stable")][:TOP_SIGNALS]
        for c in order:
            window_mean, normal_mean = y3(x[c]), y3(ref.signal_centre[c])
            deviating.append(
                {
                    "signal": feats.channels[c],
                    "window_mean": window_mean,
                    "normal_mean": normal_mean,
                    "z": y3(z[c]),
                    "difference": _difference(window_mean, normal_mean),
                }
            )
    missing = int(((~present) & (ref.signal_share >= 0.5)).sum()) if ref.size else 0
    gap = None
    g = feats.gaps[i]
    usable_gaps = ~np.isnan(g) & (ref.gap_count >= MIN_REFERENCE) & (ref.gap_spread > MIN_SPREAD)
    if usable_gaps.any():
        gz = np.where(
            usable_gaps, (g - ref.gap_centre) / np.where(usable_gaps, ref.gap_spread, 1), 0
        )
        best = int(np.flatnonzero(usable_gaps)[np.argmax(gz[usable_gaps])])
        planned, measured = feats.pairs[best]
        largest, typical = y3(g[best]), y3(ref.gap_centre[best])
        gap = {
            "pair": pair_name(planned, measured),
            "max_abs_gap": largest,
            "normal_typical": typical,
            "z": y3(gz[best]),
            "excess_over_typical": _difference(largest, typical),
        }
    candidates = [(abs(s["z"]), s["signal"]) for s in deviating]
    if gap is not None:
        candidates.append((abs(gap["z"]), gap["pair"]))
    return WindowFacts(
        window_id=window.window_id,
        ms_id=window.ms_id,
        line=window.line,
        task=window.task,
        reference=ref.description,
        deviating=deviating,
        gap=gap,
        missing_signals=missing,
        look_first=max(candidates)[1] if candidates else None,
    )


def compute_facts(
    pool: Pool, feats: Features | None = None, scale: Scale = mean_std
) -> dict[str, WindowFacts]:
    """Facts for every pool window, keyed by window id."""
    feats = feats or features(pool)
    keys, masks = _reference_masks(pool)
    references = {key: _reference(*masks[key], feats, scale) for key in masks}
    return {
        window.window_id: _window_facts(i, window, references[keys[i]], feats)
        for i, window in enumerate(pool.windows)
    }
