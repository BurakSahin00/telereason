import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from telereason.api.schemas import WINDOW_ID_PATTERN
from telereason.night.channels import pair_name, plan_measured_pairs, setting_b, vocabulary
from telereason.night.windows import (
    _mixed_values,
    _uniform_values,
    assign_window_ids,
    aursad_block,
    base_window_id,
    load_pool,
    parse_series,
)

ACRONYMS = {"fpo0": "feedback_pos_0", "spo0": "setpoint_pos_0", "ev": "event", "jt0": "joint_temp"}


def _item(
    item_id: str,
    dataset: str,
    episode: str,
    *,
    root_cause: str | None = "normal",
    template_id: int = 1,
    rows: list[str] | None = None,
) -> dict:
    return {
        "id": item_id,
        "level": 4,
        "template_id": template_id,
        "root_cause": root_cause,
        "provenance": {
            "dataset": dataset,
            "episode": episode,
            "task": "",
            "subseries_start_index": 3,
            "subseries_length": 2,
        },
        "context": {
            "time_series_format": {"acronym_mapping": ACRONYMS},
            "time_series": rows
            or [
                "t=100: fpo0=1.0, spo0=1.5, ev=2, jt0=30",
                "t=200: fpo0=2.0, spo0=2.5, ev=2, jt0=31",
            ],
        },
    }


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")


def _dataset(tmp_path: Path) -> Path:
    root = tmp_path / "factorybench"
    pool = [
        _item("fw-normal", "factorywave", "ep-a", root_cause="normal"),
        _item("fw-fault", "factorywave", "ep-b", root_cause="Collision Foam-Object"),
        _item("au-1", "aursad", "experiment_1_1", root_cause="missing_screw"),
        _item("au-2", "aursad", "experiment_4094_4094"),
        _item("voraus", "vorausad", "vo-1"),
        _item("in-lite", "factorywave", "ep-lite"),
        _item("no-label", "factorywave", "ep-c", root_cause=None),
        _item("template-2", "factorywave", "ep-d", template_id=2),
    ]
    _write_jsonl(root / "factorybench_qa" / "level_4.jsonl", pool)
    lite = [{"id": "lite-1", "provenance": {"episodes": [{"episode_a": "ep-lite"}]}}]
    _write_jsonl(root / "factorybench_lite" / "level_1.jsonl", lite)
    for level in (2, 3, 4):
        _write_jsonl(root / "factorybench_lite" / f"level_{level}.jsonl", [])
    episodes = pa.table(
        {
            "id": ["ep-a", "ep-b", "ep-lite", "ep-c"],
            "created_at": [datetime(2026, 4, 7, 9), datetime(2026, 4, 8, 23, 59)] * 2,
            "episode_metadata": [
                "{'task': 'pick_and_place', 'fault_id': 0}",
                "{'task': 'screwing'}",
                "{'task': 'screwing'}",
                "{}",
            ],
        }
    )
    (root / "factorywave").mkdir(parents=True)
    pq.write_table(episodes, root / "factorywave" / "episodes.parquet")
    return tmp_path


def test_pool_drops_voraus_lite_unlabelled_and_other_templates(tmp_path: Path) -> None:
    pool = load_pool(_dataset(tmp_path))

    assert pool.counts == {
        "read": 7,
        "voraus": 1,
        "lite": 1,
        "unlabelled": 1,
        "kept": 4,
        "renamed": 0,
    }
    assert sorted(w.ms_id for w in pool.windows) == ["au-1", "au-2", "fw-fault", "fw-normal"]
    assert all("voraus" not in w.ms_id for w in pool.windows)


def test_pool_window_fields(tmp_path: Path) -> None:
    pool = load_pool(_dataset(tmp_path))
    by_ms = {w.ms_id: w for w in pool.windows}

    for window in pool.windows:
        assert re.match(WINDOW_ID_PATTERN, window.window_id)
        assert window.window_id == base_window_id(window.ms_id)
        assert pool.get(window.window_id) is window
    assert len({w.window_id for w in pool.windows}) == len(pool.windows)
    assert (by_ms["fw-normal"].line, by_ms["fw-normal"].task) == ("FactoryWave", "pick_and_place")
    assert by_ms["fw-normal"].group == "gun:2026-04-07"
    assert by_ms["fw-fault"].group == "gun:2026-04-08"
    assert by_ms["fw-fault"].label == "collision_foam_object"
    assert (by_ms["au-1"].line, by_ms["au-1"].task, by_ms["au-1"].group) == (
        "AURSAD",
        "aursad",
        "au:b00",
    )
    assert by_ms["au-2"].group == "au:b19"
    assert (by_ms["au-1"].start, by_ms["au-1"].length) == (3, 2)


def test_pool_channels_follow_setting_b(tmp_path: Path) -> None:
    pool = load_pool(_dataset(tmp_path))

    assert pool.channels == ["feedback_pos_0", "setpoint_pos_0"]
    assert pool.pairs == [("setpoint_pos_0", "feedback_pos_0")]


def test_window_id_matches_11_oct_formula() -> None:
    # crc32("abc") = 891568578
    assert base_window_id("abc") == "W-568578"


def _collision() -> tuple[str, str]:
    seen: dict[str, str] = {}
    for n in range(100_000):
        ms_id = f"item-{n}"
        base = base_window_id(ms_id)
        if base in seen:
            return seen[base], ms_id
        seen[base] = ms_id
    raise AssertionError("no collision found")


def test_colliding_ids_keep_first_and_rehash_the_rest() -> None:
    first, second = sorted(_collision())
    others = [f"other-{n}" for n in range(50)]

    ids = assign_window_ids([second, first, *others])

    assert ids[first] == base_window_id(first)
    assert ids[second] != base_window_id(second)
    assert re.match(WINDOW_ID_PATTERN, ids[second])
    assert len(set(ids.values())) == len(ids)
    assert all(ids[ms_id] == base_window_id(ms_id) for ms_id in others)


def test_aursad_blocks() -> None:
    assert aursad_block("experiment_1_1") == "au:b00"
    assert aursad_block("experiment_205") == "au:b00"
    assert aursad_block("experiment_206_3") == "au:b01"
    assert aursad_block("experiment_4094_4094") == "au:b19"
    assert aursad_block("not-an-experiment") is None


def test_parse_series_maps_acronyms_and_drops_non_numbers() -> None:
    item = _item(
        "x",
        "factorywave",
        "ep",
        rows=["t=0: fpo0=1.5, ev=on", "t=bad: fpo0=9", "t=100: spo0=2, fpo0=nan"],
    )

    t_ms, channels, values = parse_series(item)

    assert t_ms.tolist() == [0.0, 100.0]
    assert channels == ("feedback_pos_0", "setpoint_pos_0")
    assert np.array_equal(values, np.array([[1.5, np.nan], [np.nan, 2.0]]), equal_nan=True)


def test_fast_and_general_parsers_agree() -> None:
    bodies = ["a=1, b=-2.5, c=inf", "a=3, b=4, c=5"]

    keys, values = _uniform_values(bodies) or ([], np.zeros(0))

    assert keys == ["a", "b", "c"]
    general_keys, general = _mixed_values(bodies)
    assert keys == general_keys
    assert np.array_equal(values, general, equal_nan=True)
    assert _uniform_values(["a=1, b=2", "b=1, a=2"]) is None
    assert _uniform_values(["a=1, b=x"]) is None


def test_setting_b_drops_event_state_and_temperature() -> None:
    names = [
        "event",
        "robot_mode",
        "joint_mode_3",
        "digital_input_bits",
        "joint_temp_2",
        "feedback_pos_0",
        "task_phase_extra",
    ]

    assert setting_b(names) == ["feedback_pos_0", "task_phase_extra"]


def test_vocabulary_ranks_by_count_then_name() -> None:
    rows = [["b", "a", "t"], ["b", "c"], ["a", "b"]]

    assert vocabulary(rows) == ["b", "a", "c"]
    assert vocabulary(rows, size=1) == ["b"]


def test_plan_measured_pairs() -> None:
    names = [
        "setpoint_pos_0",
        "feedback_pos_0",
        "target_speed_1",
        "actual_speed_1",
        "effort_current_0",
        "setpoint_acc_5",
    ]

    pairs = plan_measured_pairs(names)

    assert pairs == [("setpoint_pos_0", "feedback_pos_0"), ("target_speed_1", "actual_speed_1")]
    assert pair_name(*pairs[0]) == "feedback_pos_0 vs setpoint_pos_0"
