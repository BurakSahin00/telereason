import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from telereason.night.folds import Fold, LineSetup
from telereason.night.gate import (
    GATE_FILE,
    SCORES_FILE,
    jobs,
    k6_permutations,
    pick_device,
    requested_device,
    run_gate,
)
from telereason.night.windows import Pool, Window

BACKEND = Path(__file__).resolve().parents[1]


def test_jobs_are_111_trainings_per_line_with_the_defaults() -> None:
    setup = LineSetup(line="AURSAD", prior="frequency", folds=[Fold([0], [1])] * 5, groups={0: "a"})

    assert len(jobs(setup, 19)) == 111
    assert jobs(setup, 19)[15] == ("repeat", 0, 0, -1)


def test_settings_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GATE_DEVICE", raising=False)
    monkeypatch.delenv("GATE_K6_PERMUTATIONS", raising=False)
    assert requested_device() == "auto"
    assert k6_permutations() == 19

    monkeypatch.setenv("GATE_DEVICE", "CPU")
    monkeypatch.setenv("GATE_K6_PERMUTATIONS", "2")
    assert requested_device() == "cpu"
    assert k6_permutations() == 2
    assert pick_device("cpu") == "cpu"

    monkeypatch.setenv("GATE_DEVICE", "tpu")
    with pytest.raises(ValueError):
        requested_device()


# XGBoost on a tiny synthetic pool


def _window(n: int, line: str, label: str, group: str, level: float) -> Window:
    rng = np.random.default_rng(n)
    values = np.column_stack([level + rng.normal(0, 0.3, 6), np.linspace(0, 1, 6)])
    return Window(
        window_id=f"W-{n:06d}",
        ms_id=f"item-{n}",
        line=line,  # type: ignore[arg-type]
        task="aursad" if line == "AURSAD" else "screwing",
        episode=f"experiment_{n}_{n}" if line == "AURSAD" else f"ep-{n}",
        start=0,
        length=6,
        label=label,
        group=group,
        t_ms=np.arange(6, dtype="float64") * 100,
        channels=("current_0", "setpoint_pos_0"),
        values=values,
    )


def _pool() -> Pool:
    windows = []
    for day in range(6):
        for j, label in enumerate(("normal", "normal", "missing_screw", "missing_screw")):
            windows.append(
                _window(
                    len(windows), "FactoryWave", label, f"gun:2026-04-{day + 1:02d}", 3.0 * (j > 1)
                )
            )
    for block in range(6):
        for j, label in enumerate(("normal", "normal", "loosening_phase", "loosening_phase")):
            windows.append(
                _window(len(windows), "AURSAD", label, f"au:b{block:02d}", 3.0 * (j > 1))
            )
    return Pool(windows=windows, counts={}, channels=["current_0", "setpoint_pos_0"], pairs=[])


def test_gate_runs_both_lines_on_the_cpu() -> None:
    pool = _pool()

    document, scores = run_gate(pool, device="cpu", shuffles=1)

    assert list(document["lines"]) == ["FactoryWave", "AURSAD"]
    assert document["device_used"] == ["cpu"]
    assert document["features"] == 16
    assert document["classes"] == ["loosening_phase", "missing_screw", "normal"]
    for line, result in document["lines"].items():
        assert result["verdict"] == "closed"  # one shuffle: K6 cannot pass
        assert result["k7"]["changed"] == 0
        assert (result["trainings"] - 1) % 4 == 0  # 3 seeds + 1 shuffle per fold, plus K7
        assert result["n_test"] == sum(1 for s in scores if s["line"] == line)
    au = [s for s in scores if s["line"] == "AURSAD"]
    assert len(au) == 24
    assert all(0.0 <= s["score"] <= 1.0 for s in scores)
    assert all(s["score"] == pytest.approx(1 - s["p_normal"]) for s in scores)
    assert document["lines"]["AURSAD"]["anomaly"]["auroc"] > 0.9


# The separate process


def _item(n: int, dataset: str, episode: str, label: str, level: float) -> dict:
    rows = [f"t={100 * t}: c0={level + 0.1 * ((n + t) % 3):.2f}, s0={t / 5:.2f}" for t in range(6)]
    return {
        "id": f"item-{n}",
        "level": 4,
        "template_id": 1,
        "root_cause": label,
        "provenance": {"dataset": dataset, "episode": episode},
        "context": {
            "time_series_format": {"acronym_mapping": {"c0": "current_0", "s0": "setpoint_pos_0"}},
            "time_series": rows,
        },
    }


def _dataset(root: Path) -> None:
    items, ids, days = [], [], []
    for day in range(5):
        for j, label in enumerate(("normal", "normal", "missing_screw")):
            n = len(items)
            items.append(_item(n, "factorywave", f"ep-{n}", label, 3.0 * (j > 1)))
            ids.append(f"ep-{n}")
            days.append(datetime(2026, 4, day + 1, 9))
    for block in range(5):
        for j, label in enumerate(("normal", "normal", "loosening_phase")):
            n = len(items)
            experiment = 1 + block * 205 + j
            items.append(
                _item(n, "aursad", f"experiment_{experiment}_{experiment}", label, 3.0 * (j > 1))
            )
    data = root / "factorybench"
    (data / "factorybench_qa").mkdir(parents=True)
    (data / "factorybench_lite").mkdir()
    (data / "factorywave").mkdir()
    lines = "".join(json.dumps(item) + "\n" for item in items)
    (data / "factorybench_qa" / "level_4.jsonl").write_text(lines, encoding="utf-8")
    for level in (1, 2, 3, 4):
        (data / "factorybench_lite" / f"level_{level}.jsonl").write_text("", encoding="utf-8")
    episodes = pa.table(
        {
            "id": ids,
            "created_at": days,
            "episode_metadata": ["{'task': 'screwing'}"] * len(ids),
        }
    )
    pq.write_table(episodes, data / "factorywave" / "episodes.parquet")


def test_gate_process_writes_gate_json_and_scores(tmp_path: Path) -> None:
    _dataset(tmp_path)
    out = tmp_path / "gate"
    env = {**os.environ, "GATE_DEVICE": "cpu", "GATE_K6_PERMUTATIONS": "0"}

    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "telereason.night.gate",
            "--out",
            str(out),
            "--data-dir",
            str(tmp_path),
        ],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )

    assert done.returncode == 0, done.stderr
    document = json.loads((out / GATE_FILE).read_text(encoding="utf-8"))
    scores = [json.loads(line) for line in (out / SCORES_FILE).read_text().splitlines()]
    assert document["device_requested"] == "cpu"
    assert document["k6_permutations"] == 0
    assert document["windows"] == 30
    assert set(document["lines"]) == {"FactoryWave", "AURSAD"}
    assert all(r["verdict"] == "closed" for r in document["lines"].values())
    assert {s["line"] for s in scores} == {"FactoryWave", "AURSAD"}
    assert all(s["window_id"].startswith("W-") for s in scores)
    assert document["seconds"]["wall"] >= document["seconds"]["total"]
