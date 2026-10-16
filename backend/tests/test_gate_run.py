"""The gate step of a night (#40, #41): the gate process, its files, and reuse on a small night."""

import asyncio
import logging
import os
import sys
import time
from pathlib import Path

import pytest

from telereason.ai.engine import FakeEngine
from telereason.night.gate import GATE_FILE, SCORES_FILE, write_outputs
from telereason.night.gate_run import (
    GATE_LOG,
    GATE_TEXTS,
    GateFailedError,
    gate_command,
    read_gate_files,
    reusable_gate,
    run_child,
    run_dir,
)
from telereason.store.db import NightDb, RunRecord, WindowRecord
from tests.test_night_steps import FakeGate, night_dataset, pipeline, run_night


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return night_dataset(tmp_path)


@pytest.fixture
def db(tmp_path: Path) -> NightDb:
    return NightDb(tmp_path / "store" / "night.sqlite")


def test_a_full_night_runs_the_gate_into_its_run_directory(data_dir: Path, db: NightDb) -> None:
    gate = FakeGate()

    run = run_night(db, FakeEngine(), pipeline(data_dir, gate=gate))

    out = run_dir(db, run.run_id)
    assert out == db.path.parent / "runs" / run.run_id
    assert gate.calls == [(out, str(data_dir))]
    assert (out / GATE_FILE).is_file() and (out / SCORES_FILE).is_file()
    assert run.status == "done"
    assert run.gate is not None
    assert [(g.line, g.verdict, g.label) for g in run.gate.lines] == [
        ("FactoryWave", "closed", GATE_TEXTS["closed"][0]),
        ("AURSAD", "screening", GATE_TEXTS["screening"][0]),
    ]


def test_a_small_night_reuses_the_latest_full_nights_gate(
    data_dir: Path, db: NightDb, caplog: pytest.LogCaptureFixture
) -> None:
    gate = FakeGate()
    steps = pipeline(data_dir, gate=gate)
    full = run_night(db, FakeEngine(), steps)

    with caplog.at_level(logging.INFO, logger="telereason.night.steps"):
        small = run_night(db, FakeEngine(), steps, max_windows=3)

    assert len(gate.calls) == 1
    assert (small.status, small.total) == ("done", 3)
    assert small.gate is not None and small.gate.run_id == small.run_id
    assert f"gate reused from run {full.run_id}" in caplog.text
    full_scores = {w.window.window_id: w.window.score for w in db.windows(full.run_id)}
    for stored in db.windows(small.run_id):
        assert stored.window.score == full_scores[stored.window.window_id]
    assert not run_dir(db, small.run_id).exists()


def test_a_small_night_reuses_the_imported_night(data_dir: Path, db: NightDb) -> None:
    # A first full run only to learn the pool's window ids and verdicts.
    first = run_night(db, FakeEngine(), pipeline(data_dir))
    assert first.gate is not None
    windows = [s.window for s in db.windows(first.run_id)]
    imported = [
        WindowRecord(w.window_id, w.line, w.task, w.seq, score=0.5 if w.score else None)
        for w in windows
    ]
    db.replace_run(
        RunRecord(
            run_id="2026-10-11-amd",
            status="done",
            source="import",
            finished_at="2099-01-01",
            gate=first.gate,
        ),
        imported,
        [],
    )
    gate = FakeGate()

    small = run_night(db, FakeEngine(), pipeline(data_dir, gate=gate), max_windows=4)

    assert gate.calls == []
    assert small.status == "done"
    scored = [s.window.score for s in db.windows(small.run_id) if s.window.line == "AURSAD"]
    assert scored and set(scored) == {0.5}


def test_gate_mode_full_runs_the_gate_on_a_small_night(data_dir: Path, db: NightDb) -> None:
    gate = FakeGate()
    run_night(db, FakeEngine(), pipeline(data_dir, gate=gate))

    small = run_night(
        db, FakeEngine(), pipeline(data_dir, gate=gate, gate_mode="full"), max_windows=3
    )

    assert len(gate.calls) == 2
    assert (run_dir(db, small.run_id) / GATE_FILE).is_file()


def test_without_a_finished_night_a_small_night_runs_the_gate(data_dir: Path, db: NightDb) -> None:
    gate = FakeGate()

    small = run_night(db, FakeEngine(), pipeline(data_dir, gate=gate), max_windows=3)

    assert len(gate.calls) == 1
    assert (small.status, small.total) == ("done", 3)


def test_a_small_night_does_not_reuse_another_small_night(data_dir: Path, db: NightDb) -> None:
    gate = FakeGate()
    steps = pipeline(data_dir, gate=gate)
    full = run_night(db, FakeEngine(), steps)
    run_night(db, FakeEngine(), steps, max_windows=3)

    reused = reusable_gate(db)

    assert reused is not None and reused.source_run == full.run_id
    assert len(reused.windows) == 11


def test_the_gate_class_labels_come_from_gate_json(data_dir: Path, db: NightDb) -> None:
    engine = FakeEngine()
    engine.script(*["<|channel|>final<|message|>A secret fault class.<|return|>"] * 22)
    gate = FakeGate(classes=["normal", "secret_fault_class"])

    run = run_night(db, engine, pipeline(data_dir, gate=gate))

    attempts = db.notes(run.run_id, db.windows(run.run_id)[0].window.window_id)
    assert "secret fault class" in attempts[0].violations


def test_gate_files_become_the_gate_output(tmp_path: Path) -> None:
    document = {
        "classes": ["normal", "missing_screw"],
        "lines": {
            "FactoryWave": {"decision": "unmeasurable", "verdict": "closed", "anomaly": {}},
            "AURSAD": {
                "decision": "pass",
                "verdict": "open",
                "anomaly": {"auroc": 0.95, "interval": [0.9, 0.97]},
            },
        },
    }
    scores = [{"window_id": "W-000001", "line": "AURSAD", "fold": 2, "score": 0.8}]
    write_outputs(tmp_path, document, scores)

    output = read_gate_files(tmp_path, "run-1")

    # A line without a measured AUROC is left out (no invented number), so it stays closed.
    assert [(g.line, g.verdict, g.auroc, g.auroc_ci) for g in output.verdicts.lines] == [
        ("AURSAD", "open", 0.95, (0.9, 0.97))
    ]
    assert output.verdicts.lines[0].label == GATE_TEXTS["open"][0]
    assert output.windows["W-000001"].score == 0.8 and output.windows["W-000001"].fold == 2
    assert output.classes == ("normal", "missing_screw")


def test_missing_gate_files_fail_the_step(tmp_path: Path) -> None:
    with pytest.raises(GateFailedError, match=GATE_FILE):
        read_gate_files(tmp_path, "run-1")


def test_the_gate_runs_with_the_apps_python(tmp_path: Path) -> None:
    command = gate_command(tmp_path / "out", "/data")

    assert command[:3] == (sys.executable, "-m", "telereason.night.gate")
    assert command[3:] == ("--out", str(tmp_path / "out"), "--data-dir", "/data")


def test_the_child_gets_the_environment_and_writes_its_log(tmp_path: Path) -> None:
    script = "import os; print(os.environ['DATA_DIR'], os.environ['GATE_DEVICE'])"

    asyncio.run(
        run_child(
            (sys.executable, "-c", script),
            tmp_path,
            {**os.environ, "DATA_DIR": "/data", "GATE_DEVICE": "cpu"},
        )
    )

    assert (tmp_path / GATE_LOG).read_text().split() == ["/data", "cpu"]


def test_a_failing_child_raises_with_the_end_of_its_log(tmp_path: Path) -> None:
    script = "import sys; print('training failed'); sys.exit(3)"

    with pytest.raises(GateFailedError, match="code 3: training failed"):
        asyncio.run(run_child((sys.executable, "-c", script), tmp_path, os.environ))


def test_a_cancelled_run_stops_the_child(tmp_path: Path) -> None:
    marker = tmp_path / "still-running"
    script = f"import time; time.sleep(3); open({str(marker)!r}, 'w').close()"

    async def cancel() -> float:
        task = asyncio.create_task(run_child((sys.executable, "-c", script), tmp_path, os.environ))
        await asyncio.sleep(0.5)
        started = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return time.monotonic() - started

    waited = asyncio.run(cancel())
    time.sleep(3.5)

    assert waited < 2
    assert not marker.exists()
