from dataclasses import replace

import numpy as np
import pytest

from telereason.night.features import STATS, feature_names, gate_features, window_features
from telereason.night.folds import (
    assign_folds,
    aursad_setup,
    calibration_groups,
    day_group,
    factorywave_setup,
)
from telereason.night.windows import Pool, Window

NAN = float("nan")


def make_window(
    n: int,
    line: str,
    label: str,
    group: str,
    *,
    task: str = "screwing",
    values: list[list[float]] | None = None,
) -> Window:
    rows = np.array(values or [[1.0, 2.0], [2.0, 2.0]], dtype="float64")
    return Window(
        window_id=f"W-{n:06d}",
        ms_id=f"item-{n}",
        line=line,  # type: ignore[arg-type]
        task="aursad" if line == "AURSAD" else task,
        episode=f"experiment_{n}_{n}" if line == "AURSAD" else f"ep-{n}",
        start=0,
        length=len(rows),
        label=label,
        group=group,
        t_ms=np.arange(len(rows), dtype="float64") * 100,
        channels=("current_0", "setpoint_pos_0"),
        values=rows,
    )


# Features


def test_window_features_summarise_present_rows() -> None:
    window = make_window(1, "FactoryWave", "normal", "gun:2026-04-07")
    window = replace(window, values=np.array([[1.0, NAN], [NAN, NAN], [2.0, NAN], [6.0, NAN]]))

    out = window_features(window, ["current_0", "missing", "setpoint_pos_0"]).reshape(3, 8)

    values = np.array([1.0, 2.0, 6.0])
    slope = np.polyfit(np.arange(3), values, 1)[0]
    assert out[0] == pytest.approx([3.0, values.std(), 1.0, 6.0, 1.0, 6.0, slope, 1.0])
    assert out[1].tolist() == [0.0] * 8  # channel not in the window
    assert out[2].tolist() == [0.0] * 8  # channel present but every value missing


def test_single_value_has_zero_slope() -> None:
    window = make_window(1, "AURSAD", "normal", "au:b00", values=[[4.0, 1.0]])

    out = window_features(window, ["current_0"])

    assert out.tolist() == [4.0, 0.0, 4.0, 4.0, 4.0, 4.0, 0.0, 1.0]


def test_gate_features_are_float32_with_named_columns() -> None:
    windows = [make_window(i, "FactoryWave", "normal", "gun:2026-04-07") for i in range(3)]
    pool = Pool(windows=windows, counts={}, channels=["current_0", "setpoint_pos_0"], pairs=[])

    x = gate_features(pool)

    assert x.shape == (3, 2 * len(STATS))
    assert x.dtype == np.float32
    assert feature_names(pool.channels)[:2] == ["current_0.mean", "current_0.std"]
    assert feature_names(pool.channels)[-1] == "setpoint_pos_0.present"


# Folds


def test_assign_folds_balances_window_counts() -> None:
    groups = ["a"] * 5 + ["b"] * 4 + ["c"] * 3 + ["d"] * 2 + ["e"] * 1 + ["f"] * 1

    folds = assign_folds(groups, folds=3)

    load = [0, 0, 0]
    for group, fold in folds.items():
        load[fold] += groups.count(group)
    assert folds["a"] == 0 and folds["b"] == 1 and folds["c"] == 2
    assert sorted(load) == [5, 5, 6]
    assert assign_folds(groups, folds=3) == folds


def test_calibration_groups_take_a_share_and_skip_large_groups() -> None:
    groups = ["big"] * 40 + [g for g in "abcdefghij" for _ in range(6)]

    chosen = calibration_groups(groups)

    assert "big" not in chosen
    assert sum(groups.count(g) for g in chosen) >= 0.15 * len(groups)
    assert len(chosen) < len(set(groups))


def test_calibration_keeps_one_group_for_training() -> None:
    assert len(calibration_groups(["a", "b"], share=0.9)) == 1
    assert calibration_groups(["a"]) == set()


def test_day_group_is_episode_for_windows_without_a_day() -> None:
    fw = make_window(1, "FactoryWave", "normal", "gun:2026-04-07")
    au = make_window(2, "AURSAD", "normal", "au:b03")

    assert day_group(fw) == "gun:2026-04-07"
    assert day_group(au) == "?experiment_2_2"


def _windows() -> list[Window]:
    windows = []
    n = 0
    for day in range(8):
        for label in ("normal", "normal", "missing_screw"):
            windows.append(make_window(n, "FactoryWave", label, f"gun:2026-04-{day + 1:02d}"))
            n += 1
    # A class seen on one day only is never tested: its day's fold has no training example.
    windows.append(make_window(n, "FactoryWave", "hole_obstruction", "gun:2026-04-01"))
    n += 1
    for block in range(6):
        for label in ("normal", "damaged_screw_thread"):
            windows.append(make_window(n, "AURSAD", label, f"au:b{block:02d}"))
            n += 1
    return windows


def test_factorywave_folds_split_by_day() -> None:
    windows = _windows()
    classes = sorted({w.label for w in windows})
    labels = [classes.index(w.label) for w in windows]

    setup = factorywave_setup(windows, labels)

    assert setup.line == "FactoryWave" and setup.prior == "task"
    tested = [i for fold in setup.folds for i in fold.test]
    assert len(tested) == len(set(tested))
    for fold in setup.folds:
        test_days = {setup.groups[i] for i in fold.test}
        assert not test_days & {setup.groups[i] for i in fold.fit}
        assert all(windows[i].line == "FactoryWave" for i in fold.test)
        assert {labels[i] for i in fold.test} <= {labels[i] for i in fold.fit}
        # AURSAD windows train every FactoryWave fold unless held out for calibration.
        assert any(windows[i].line == "AURSAD" for i in fold.fit)
    assert all(windows[i].label != "hole_obstruction" for i in tested)
    assert set(setup.pool) == set(range(len(windows)))


def test_factorywave_folds_hold_out_calibration_groups() -> None:
    windows = _windows()
    labels = [0 if w.label == "normal" else 1 for w in windows]

    setup = factorywave_setup(windows, labels)

    for fold in setup.folds:
        used = set(fold.fit) | set(fold.test)
        assert len(used) < len(windows)


def test_aursad_folds_split_by_block() -> None:
    windows = _windows()

    setup = aursad_setup(windows)

    assert setup.line == "AURSAD" and setup.prior == "frequency"
    assert len(setup.folds) == 5
    tested = sorted(i for fold in setup.folds for i in fold.test)
    assert tested == [i for i, w in enumerate(windows) if w.line == "AURSAD"]
    for fold in setup.folds:
        assert not {windows[i].group for i in fold.test} & {windows[i].group for i in fold.fit}
        assert set(fold.fit) | set(fold.test) == set(tested)
