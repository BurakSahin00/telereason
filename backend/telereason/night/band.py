"""Time series and normal band for the window chart (``GET /api/windows/{window_id}/signals``).

The band uses the same normal windows and the same spread as the facts: each reference window's
channel is stretched linearly onto the window's length, and at every step the band is
median +/- 2 x max(1.4826 x MAD, floor), where the floor is the line-wide one from
``telereason.night.robust``.  2 is where ``plain`` turns from "Within the normal range" to
"Slightly off normal".  A channel with fewer than 5 reference windows has no band and is left
out.  A plan-versus-measured name (``feedback_pos_5 vs setpoint_pos_5``, as in the facts) is
served as one derived channel: measured minus planned.

The pool, its facts and its references are built once, on the first request (about half a
minute on a laptop), and kept in memory.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path

import numpy as np

from telereason.api.schemas import SignalChannel, SignalsResponse
from telereason.data.paths import dataset_dir
from telereason.night.channels import pair_name
from telereason.night.facts import (
    MIN_REFERENCE,
    Features,
    WindowFacts,
    compute_facts,
    features,
    line_floors,
    reference_masks,
)
from telereason.night.robust import median_scale
from telereason.night.windows import POOL_FILE, Pool, Window, load_pool

log = logging.getLogger(__name__)

BAND_Z = 2.0
DEFAULT_EXTRA = 2


class PoolNotAvailableError(RuntimeError):
    """The L4-t1 pool has not been downloaded."""


class UnknownWindowError(KeyError):
    """The window id is not in the pool."""


def resample(values: np.ndarray, length: int) -> np.ndarray | None:
    """Stretch a series with NaN gaps linearly onto ``length`` steps; None without a value."""
    finite = ~np.isnan(values)
    if not finite.any() or length < 1:
        return None
    position = np.linspace(0.0, 1.0, len(values)) if len(values) > 1 else np.zeros(1)
    target = np.linspace(0.0, 1.0, length) if length > 1 else np.zeros(1)
    return np.interp(target, position[finite], values[finite])


def normal_band(
    references: list[np.ndarray], length: int, floor: float
) -> tuple[np.ndarray, np.ndarray] | None:
    """(low, high) per step from the reference series, or None with too few references."""
    stretched = [series for r in references if (series := resample(r, length)) is not None]
    if len(stretched) < MIN_REFERENCE:
        return None
    centre, spread = median_scale(np.vstack(stretched), np.full(length, floor))
    return centre - BAND_Z * spread, centre + BAND_Z * spread


def default_channels(facts: WindowFacts) -> list[str]:
    """``look_first`` (both channels of a pair) plus the next two most deviating signals."""
    chosen: list[str] = []
    if facts.look_first:
        chosen += facts.look_first.split(" vs ")
    extra = [s["signal"] for s in facts.deviating if s["signal"] not in chosen]
    return chosen + extra[:DEFAULT_EXTRA]


@dataclass
class NightData:
    pool: Pool
    feats: Features
    facts: dict[str, WindowFacts]
    keys: list[tuple[str, ...]]
    masks: dict
    floors: dict[str, tuple[np.ndarray, np.ndarray]]
    index: dict[str, int]


class NightSignals:
    """Signals and normal band of pool windows, read from ``DATA_DIR/factorybench``."""

    def __init__(self, data_dir: str | Path) -> None:
        self.pool_path = dataset_dir(data_dir) / POOL_FILE
        self.data_dir = data_dir
        self._night: NightData | None = None
        self._lock = threading.Lock()

    def _load(self) -> NightData:
        with self._lock:
            if self._night is None:
                if not self.pool_path.exists():
                    raise PoolNotAvailableError(
                        f"No night pool at {self.pool_path}; run telereason.data.download first."
                    )
                pool = load_pool(self.data_dir)
                feats = features(pool)
                keys, masks = reference_masks(pool)
                self._night = NightData(
                    pool=pool,
                    feats=feats,
                    facts=compute_facts(pool, feats),
                    keys=keys,
                    masks=masks,
                    floors=line_floors(pool, feats),
                    index={w.window_id: i for i, w in enumerate(pool.windows)},
                )
                log.info("night signals ready: %d windows", len(pool.windows))
            return self._night

    def load(self) -> NightData:
        """The pool, its facts and references; built on the first call, then kept."""
        return self._load()

    def get(self, window_id: str, channels: list[str] | None = None) -> SignalsResponse:
        night = self._load()
        i = night.index.get(window_id)
        if i is None:
            raise UnknownWindowError(window_id)
        window = night.pool.windows[i]
        names = channels or default_channels(night.facts[window_id])
        _, mask = night.masks[night.keys[i]]
        references = [night.pool.windows[j] for j in np.flatnonzero(mask)]
        signal_floors, gap_floors = night.floors[window.line]
        pairs = {
            pair_name(planned, measured): k
            for k, (planned, measured) in enumerate(night.feats.pairs)
        }
        rows = []
        for name in dict.fromkeys(names):
            if name in night.feats.channels:
                floor = float(signal_floors[night.feats.channels.index(name)])
                row = _channel(name, window, references, floor, lambda w, n=name: w.column(n))
            elif name in pairs:
                planned, measured = night.feats.pairs[pairs[name]]
                floor = float(gap_floors[pairs[name]])
                row = _channel(name, window, references, floor, partial(_gap, planned, measured))
            else:
                row = None
            if row is not None:
                rows.append(row)
        t_s = (window.t_ms - window.t_ms[0]) / 1000 if len(window.t_ms) else window.t_ms
        return SignalsResponse(window_id=window_id, t_s=t_s.tolist(), channels=rows)


def _gap(planned: str, measured: str, window: Window) -> np.ndarray | None:
    """Measured minus planned, the series behind a plan-versus-measured fact."""
    plan, actual = window.column(planned), window.column(measured)
    return None if plan is None or actual is None else actual - plan


def _channel(
    name: str,
    window: Window,
    references: list[Window],
    floor: float,
    series_of: Callable[[Window], np.ndarray | None],
) -> SignalChannel | None:
    column = series_of(window)
    values = resample(column, len(column)) if column is not None else None
    if values is None:
        return None
    series = [c for r in references if (c := series_of(r)) is not None]
    band = normal_band(series, len(values), floor)
    if band is None:
        return None
    low, high = band
    return SignalChannel(
        name=name, values=values.tolist(), normal_low=low.tolist(), normal_high=high.tolist()
    )
