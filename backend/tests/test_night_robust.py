import numpy as np

from telereason.night.facts import compute_facts, y3
from telereason.night.robust import MAD_SCALE, floor, median_mad, median_scale, robust_z
from telereason.night.windows import Pool, Window


def test_median_mad_ignores_missing_values() -> None:
    values = np.array([[1.0, np.nan], [2.0, 4.0], [100.0, 6.0], [np.nan, 8.0]])

    centre, spread = median_mad(values)

    assert centre.tolist() == [2.0, 6.0]
    assert spread.tolist() == [MAD_SCALE * 1.0, MAD_SCALE * 2.0]


def test_floor_is_the_line_spread_and_zero_when_empty() -> None:
    values = np.array([[0.0, np.nan], [2.0, np.nan]])

    assert floor(values).tolist() == [1.0, 0.0]


def test_median_scale_uses_the_floor_when_mad_is_smaller() -> None:
    values = np.array([[5.0], [5.0], [5.0], [6.0]])

    centre, spread = median_scale(values, np.array([0.5]))

    assert centre.tolist() == [5.0]
    assert spread.tolist() == [0.5]


def test_constant_signal_has_finite_zero_z() -> None:
    values = np.full((6, 1), 3.0)
    centre, spread = median_scale(values, floor(values))

    z = robust_z(np.array([3.0]), centre, spread)

    assert np.isfinite(z).all()
    assert z.tolist() == [0.0]
    assert robust_z(np.array([np.nan]), centre, spread).tolist() == [0.0]


def _window(window_id: str, group: str, label: str, columns: dict[str, float]) -> Window:
    channels = tuple(columns)
    values = np.array([[columns[name] for name in channels]] * 2, dtype="float64")
    return Window(
        window_id=window_id,
        ms_id=f"item-{window_id}",
        line="AURSAD",
        task="aursad",
        episode=f"ep-{window_id}",
        start=0,
        length=2,
        label=label,
        group=group,
        t_ms=np.array([0.0, 100.0]),
        channels=channels,
        values=values,
    )


def test_robust_facts_resist_an_outlier_and_skip_constant_signals() -> None:
    reference = [1.0, 2.0, 3.0, 4.0, 5.0, 1000.0]
    windows = [
        _window(f"W-00000{n}", "au:b01", "normal", {"moving": a, "constant": 7.0})
        for n, a in enumerate(reference)
    ]
    windows.append(
        _window("W-000100", "au:b00", "missing_screw", {"moving": 13.5, "constant": 7.0})
    )
    pool = Pool(windows=windows, counts={}, channels=["moving", "constant"], pairs=[])

    facts = compute_facts(pool)["W-000100"]

    median = float(np.median(reference))
    mad = MAD_SCALE * float(np.median(np.abs(np.array(reference) - median)))
    line_std = float(np.std([*reference, 13.5]))
    assert [s["signal"] for s in facts.deviating] == ["moving"]
    # The outlier leaves the median alone; the spread is the larger of MAD and the line floor.
    assert facts.deviating[0]["normal_mean"] == median
    assert facts.deviating[0]["z"] == y3((13.5 - median) / max(mad, line_std))
    assert facts.look_first == "moving"
