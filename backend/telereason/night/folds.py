"""Grouped folds for the gate, as in the 11 Oct gate.

FactoryWave is split by recording day, AURSAD by experiment block, so a test window never
shares its day or block with a training window.

- Groups go to 5 folds with balanced window counts: largest group first, ties in SHA-256
  order, each group to the fold with the fewest windows so far.
- FactoryWave: the test windows of a fold are the windows of its days whose label occurs in
  training.  Training is every other window, AURSAD included (AURSAD windows have no day and
  are never tested on this line), minus the calibration days: groups taken in SHA-256 order
  until they hold 15 % of the training windows.  Calibration windows are not used by the gate.
- AURSAD: the test windows of a fold are its blocks' windows; training is the other AURSAD
  windows.

The SHA-256 order is seeded with 25, the 11 Oct seed, so the same groups give the same folds.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from telereason.api.schemas import Line
from telereason.night.windows import Window

FOLDS = 5
FOLD_SEED = 25
CALIBRATION_SHARE = 0.15
# A group holding more than this share of the training windows is never a calibration group.
CALIBRATION_MAX_GROUP = 0.3

Prior = Literal["task", "frequency"]


def _order(*parts: object) -> str:
    # The salts "kat" (fold) and "kal" (calibration) are the 11 Oct ones; they fix the order.
    return hashlib.sha256(":".join(str(p) for p in parts).encode()).hexdigest()


def assign_folds(
    groups: Sequence[str], folds: int = FOLDS, seed: int = FOLD_SEED
) -> dict[str, int]:
    """Group -> fold, balancing window counts (see the module docstring)."""
    counts = Counter(groups)
    load = [0] * folds
    out: dict[str, int] = {}
    for group in sorted(counts, key=lambda g: (-counts[g], _order(seed, "kat", g))):
        fold = min(range(folds), key=lambda i: (load[i], i))
        out[group] = fold
        load[fold] += counts[group]
    return out


def calibration_groups(
    groups: Sequence[str], share: float = CALIBRATION_SHARE, seed: int = FOLD_SEED
) -> set[str]:
    """Groups in SHA-256 order until they hold ``share`` of the windows; one group stays out."""
    counts = Counter(groups)
    total = sum(counts.values())
    chosen: set[str] = set()
    taken = 0
    for group in sorted(counts, key=lambda g: _order(seed, "kal", g)):
        if taken >= share * total or len(chosen) >= len(counts) - 1:
            break
        if counts[group] > CALIBRATION_MAX_GROUP * total:
            continue
        chosen.add(group)
        taken += counts[group]
    if not chosen and len(counts) > 1:
        chosen.add(min(counts, key=lambda g: (counts[g], _order(seed, "kal", g))))
    return chosen


def day_group(window: Window) -> str:
    """Recording day of a FactoryWave window; any other window keeps its episode behind ``?``."""
    return window.group if window.line == "FactoryWave" else f"?{window.episode}"


@dataclass(frozen=True)
class Fold:
    fit: list[int]
    test: list[int]


@dataclass(frozen=True)
class LineSetup:
    """What the gate trains and tests for one line; indices point into the pool."""

    line: Line
    prior: Prior
    folds: list[Fold]
    # Group of every window whose label the K6 shuffle permutes (the line's pool).
    groups: dict[int, str]

    @property
    def pool(self) -> list[int]:
        return sorted(self.groups)


def factorywave_setup(windows: Sequence[Window], labels: Sequence[int]) -> LineSetup:
    days = [day_group(w) for w in windows]
    known = [i for i, day in enumerate(days) if not day.startswith("?")]
    fold_of = assign_folds([days[i] for i in known])
    folds = []
    for k in range(FOLDS):
        test_days = {i for i in known if fold_of[days[i]] == k}
        training = [i for i in range(len(windows)) if i not in test_days]
        held_out = calibration_groups([days[i] for i in training])
        fit = [i for i in training if days[i] not in held_out]
        seen = {labels[i] for i in fit}
        test = [i for i in sorted(test_days) if labels[i] in seen]
        if test:
            folds.append(Fold(fit=fit, test=test))
    return LineSetup(
        line="FactoryWave",
        prior="task",
        folds=folds,
        groups=dict(enumerate(days)),
    )


def aursad_setup(windows: Sequence[Window]) -> LineSetup:
    blocks = {i: w.group for i, w in enumerate(windows) if w.line == "AURSAD"}
    fold_of = assign_folds(list(blocks.values()))
    folds = []
    for k in range(FOLDS):
        test = [i for i, block in blocks.items() if fold_of[block] == k]
        fit = [i for i, block in blocks.items() if fold_of[block] != k]
        if test and fit:
            folds.append(Fold(fit=fit, test=test))
    return LineSetup(line="AURSAD", prior="frequency", folds=folds, groups=blocks)


def line_setups(windows: Sequence[Window], labels: Sequence[int]) -> list[LineSetup]:
    """FactoryWave then AURSAD; a line without a fold is left out."""
    setups = [factorywave_setup(windows, labels), aursad_setup(windows)]
    return [setup for setup in setups if setup.folds]
