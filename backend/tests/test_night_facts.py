import json

import numpy as np
import pytest

from telereason.api.schemas import Fact
from telereason.night.facts import compute_facts, mean_std, plain_reading, y3
from telereason.night.windows import Pool, Window

PAIR = ("setpoint_pos_0", "feedback_pos_0")
CHANNELS = ["sig_a", "sig_b", "flat", *PAIR]
FAULT = "missing_screw"


def _classic(pool: Pool) -> dict:
    """These tests pin the 11 Oct method, so they use its mean/std z."""
    return compute_facts(pool, scale=mean_std)


def _window(
    window_id: str,
    line: str,
    group: str,
    label: str,
    columns: dict[str, list[float]],
    task: str = "aursad",
) -> Window:
    channels = tuple(columns)
    values = np.array([columns[name] for name in channels], dtype="float64").T
    return Window(
        window_id=window_id,
        ms_id=f"item-{window_id}",
        line=line,  # type: ignore[arg-type]
        task=task,
        episode=f"ep-{window_id}",
        start=0,
        length=len(values),
        label=label,
        group=group,
        t_ms=np.arange(len(values), dtype="float64") * 100,
        channels=channels,
        values=values,
    )


def _aursad(window_id: str, group: str, label: str, a: float, gap: float, b: bool) -> Window:
    columns = {"sig_a": [a, a], "flat": [1.0, 1.0], PAIR[0]: [0.0, 0.0], PAIR[1]: [0.0, gap]}
    if b:
        columns["sig_b"] = [a, a]
    return _window(window_id, "AURSAD", group, label, columns)


def _pool(windows: list[Window]) -> Pool:
    return Pool(windows=windows, counts={}, channels=CHANNELS, pairs=[PAIR])


def _aursad_pool() -> Pool:
    normals = [
        _aursad(f"W-00000{n}", "au:b01", "normal", float(n), 0.1 * (n + 1), b=n < 4)
        for n in range(6)
    ]
    own_block = _aursad("W-000100", "au:b00", "normal", 100.0, 50.0, b=True)
    target = _aursad("W-000200", "au:b00", FAULT, 10.0, 2.0, b=True)
    return _pool([*normals, own_block, target])


def test_mean_std_ignores_missing_values() -> None:
    values = np.array([[1.0, np.nan], [3.0, 5.0], [np.nan, 7.0]])

    centre, spread = mean_std(values, np.zeros(2))

    assert centre.tolist() == [2.0, 6.0]
    assert spread.tolist() == [1.0, 1.0]


def test_y3_and_plain_reading() -> None:
    assert y3(0.12345) == 0.123
    assert y3(-1234.5) == -1230.0
    assert y3(0.000999) == 0.0
    assert y3(float("nan")) is None
    assert plain_reading(-1.88) == "Within the normal range (1.88 standard deviations)."
    assert plain_reading(2.0) == "Slightly off normal (2 standard deviations)."
    assert plain_reading(-3.0) == "Clearly off normal (3 standard deviations)."


def test_aursad_reference_is_other_blocks_and_skips_thin_or_flat_signals() -> None:
    facts = _classic(_aursad_pool())["W-000200"]

    reference = np.arange(6.0)
    expected_z = (10.0 - reference.mean()) / reference.std()
    signals = [s["signal"] for s in facts.deviating]
    # sig_b has 4 reference windows (< 5); flat and setpoint_pos_0 have no spread.
    assert signals == ["feedback_pos_0", "sig_a"]
    sig_a = facts.deviating[1]
    assert sig_a == {
        "signal": "sig_a",
        "window_mean": 10.0,
        "normal_mean": 2.5,
        "z": y3(expected_z),
        "difference": 7.5,
    }
    assert facts.reference == "other experiment blocks"
    assert facts.missing_signals == 0


def test_gap_and_look_first() -> None:
    facts = _classic(_aursad_pool())["W-000200"]

    gaps = 0.1 * np.arange(1, 7)
    assert facts.gap == {
        "pair": "feedback_pos_0 vs setpoint_pos_0",
        "max_abs_gap": 2.0,
        "normal_typical": y3(gaps.mean()),
        "z": y3((2.0 - gaps.mean()) / gaps.std()),
        "excess_over_typical": y3(2.0 - y3(gaps.mean())),
    }
    # The gap and feedback_pos_0 tie on |z|; the 11 Oct rule takes the larger name.
    assert facts.look_first == "feedback_pos_0 vs setpoint_pos_0"
    assert facts.max_abs_z == abs(facts.gap["z"])


def test_fact_rows_match_the_window_detail_schema() -> None:
    facts = _classic(_aursad_pool())["W-000200"]

    rows = facts.fact_rows()

    assert all(isinstance(row, Fact) for row in rows)
    assert [row.signal for row in rows] == [
        "feedback_pos_0",
        "sig_a",
        "feedback_pos_0 vs setpoint_pos_0",
    ]
    assert rows[-1].window_mean == facts.gap["max_abs_gap"]
    assert rows[-1].difference == facts.gap["excess_over_typical"]
    assert rows[1].plain == plain_reading(rows[1].z)


def test_note_input_keeps_the_11_oct_shape() -> None:
    facts = _classic(_aursad_pool())["W-000200"]

    closed = facts.note_input()
    screening = facts.note_input(score=0.845)

    assert list(closed) == [
        "window",
        "line",
        "task",
        "reference",
        "deviating_signals",
        "largest_plan_vs_measured_gap",
        "missing_signals",
        "look_first",
    ]
    assert list(screening)[:3] == ["window", "abnormal_score", "abnormal_score_note"]
    assert screening["abnormal_score"] == 0.845
    assert closed["reference"] == "normal windows, other experiment blocks"


def test_facts_never_carry_the_fault_label() -> None:
    facts = _classic(_aursad_pool())["W-000200"]

    text = json.dumps([facts.note_input(), [row.model_dump() for row in facts.fact_rows()]])

    assert FAULT not in text
    assert "missing" not in text.replace("missing_signals", "")


def test_missing_signals_counts_channels_normal_windows_usually_have() -> None:
    pool = _aursad_pool()
    target = pool.windows[-1]
    columns = {name: target.values[:, i].tolist() for i, name in enumerate(target.channels)}
    del columns["flat"]
    pool.windows[-1] = _window("W-000200", "AURSAD", "au:b00", FAULT, columns)

    facts = _classic(_pool(pool.windows))["W-000200"]

    # flat is in all 6 reference windows; sig_b in 4 of 6 (>= half) but the window has it.
    assert facts.missing_signals == 1


def _fw(window_id: str, day: str, task: str, label: str, a: float) -> Window:
    return _window(window_id, "FactoryWave", day, label, {"sig_a": [a, a]}, task=task)


@pytest.mark.parametrize(
    ("same_task_normals", "reference"),
    [(10, "same task, other days"), (9, "all tasks, other days")],
)
def test_factorywave_reference(same_task_normals: int, reference: str) -> None:
    windows = [
        _fw(f"W-1000{n:02d}", "gun:2026-04-08", "screwing", "normal", float(n))
        for n in range(same_task_normals)
    ]
    windows += [
        _fw(f"W-2000{n:02d}", "gun:2026-04-09", "pick_and_place", "normal", 100.0 + n)
        for n in range(5)
    ]
    windows.append(_fw("W-300000", "gun:2026-04-07", "screwing", "normal", -50.0))
    windows.append(_fw("W-400000", "gun:2026-04-07", "screwing", "collision", 4.0))

    facts = _classic(_pool(windows))["W-400000"]

    assert facts.reference == reference
    expected = np.arange(float(same_task_normals))
    if reference.startswith("all"):
        expected = np.concatenate([expected, 100.0 + np.arange(5.0)])
    assert facts.deviating[0]["normal_mean"] == y3(expected.mean())
    assert facts.task == "screwing"


def test_window_without_usable_signals_has_no_look_first() -> None:
    windows = [_fw("W-000001", "gun:2026-04-07", "screwing", "normal", 1.0)]

    facts = _classic(_pool(windows))["W-000001"]

    assert facts.deviating == []
    assert facts.gap is None
    assert facts.look_first is None
    assert facts.max_abs_z is None
    assert facts.fact_rows() == []
