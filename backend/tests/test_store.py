"""The SQLite night store (#48) on a temporary file."""

from pathlib import Path
from typing import Any

import pytest

from telereason.api.schemas import (
    Fact,
    GateLine,
    GateResponse,
    Line,
    ReportItem,
    ReportLine,
    StepTimings,
    Timings,
)
from telereason.store.db import NightDb, NoteRecord, RunRecord, WindowRecord

GATE = GateResponse(
    run_id="run-1",
    lines=[
        GateLine(
            line="AURSAD",
            verdict="screening",
            label="Abnormal-behaviour screening only",
            auroc=0.9,
            auroc_ci=(0.8, 0.95),
            explanation="Normal and faulty runs separate well.",
        )
    ],
)


@pytest.fixture
def db(tmp_path: Path) -> NightDb:
    return NightDb(tmp_path / "sub" / "night.sqlite").init()


def window(window_id: str, seq: int, *, line: Line = "AURSAD", **fields: Any) -> WindowRecord:
    task = "aursad" if line == "AURSAD" else "screwing"
    return WindowRecord(window_id=window_id, seq=seq, line=line, task=task, **fields)


def note(window_id: str, attempt: int, passed: bool, text: str = "note") -> NoteRecord:
    return NoteRecord(
        window_id=window_id, attempt=attempt, text_raw=f"**{text}**", text=text, passed=passed
    )


def test_init_creates_the_file_in_wal_mode(db: NightDb) -> None:
    with db.connect() as conn:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}

    assert db.path.exists()
    assert mode == "wal"
    assert {"night_runs", "windows", "notes", "reports"} <= tables


def test_init_twice_keeps_the_data(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="run-1", status="queued", source="live"))

    db.init()

    assert db.get_run("run-1") is not None


def test_run_round_trip_and_update(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="run-1", status="queued", source="live", max_windows=300))

    db.update_run("run-1", status="running", step="gate", total=300)
    db.update_run("run-1", gate=GATE, timings=StepTimings(gate=1.5), step="facts", done=7)
    run = db.get_run("run-1")

    assert run is not None
    assert (run.status, run.step, run.done, run.total, run.max_windows) == (
        "running",
        "facts",
        7,
        300,
        300,
    )
    assert run.step_timings == StepTimings(gate=1.5)
    assert run.gate == GATE
    assert run.timings is None
    assert db.get_run("run-2") is None


def test_timings_once_every_step_finished(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="run-1", status="running", source="live"))

    db.update_run("run-1", timings=StepTimings(gate=1, facts=2, scan=3, report=0.5), t_total=7.0)
    run = db.get_run("run-1")

    assert run is not None
    assert run.timings == Timings(gate=1, facts=2, scan=3, report=0.5, total=7)


def test_update_rejects_unknown_columns(db: NightDb) -> None:
    with pytest.raises(ValueError, match="source"):
        db.update_run("run-1", source="import")


def test_latest_finished_run_is_the_last_to_finish(db: NightDb) -> None:
    db.create_run(
        RunRecord(run_id="2026-10-11-amd", status="done", source="import", finished_at="2026-10-11")
    )
    db.create_run(
        RunRecord(
            run_id="run-20261016-1",
            status="done",
            source="live",
            finished_at="2026-10-16T09:00:00+00:00",
        )
    )
    db.create_run(RunRecord(run_id="run-20261016-2", status="failed", source="live"))
    db.create_run(RunRecord(run_id="run-20261016-3", status="running", source="live"))

    latest = db.latest_finished_run()
    active = db.active_run()

    assert latest is not None and latest.run_id == "run-20261016-1"
    assert active is not None and active.run_id == "run-20261016-3"
    assert sorted(db.run_ids("run-20261016-")) == [
        "run-20261016-1",
        "run-20261016-2",
        "run-20261016-3",
    ]


def test_fail_unfinished_marks_queued_and_running_runs(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="a", status="queued", source="live"))
    db.create_run(RunRecord(run_id="b", status="running", source="live"))
    db.create_run(RunRecord(run_id="c", status="done", source="live"))

    failed = db.fail_unfinished("stopped")

    assert sorted(failed) == ["a", "b"]
    runs = [db.get_run(run_id) for run_id in "abc"]
    assert [run.status if run else None for run in runs] == ["failed", "failed", "done"]
    assert runs[0] is not None and runs[0].error == "stopped"
    assert db.active_run() is None


def test_windows_carry_their_last_note(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="run-1", status="running", source="live"))
    fact = Fact(signal="s", window_mean=1, normal_mean=0, difference=1, z=2.5, plain="p")
    db.put_windows(
        "run-1",
        [window("W-000001", 0, facts=(fact,), score=0.95, flagged=True), window("W-000002", 1)],
    )
    db.put_notes("run-1", [note("W-000001", 1, False, "first"), note("W-000001", 2, True, "retry")])

    first = db.window("run-1", "W-000001")
    second = db.window("run-1", "W-000002")

    assert first is not None and second is not None
    assert first.window.facts == (fact,)
    assert first.window.flagged is True
    assert first.note is not None and first.note.text == "retry" and first.verified
    assert second.note is None and not second.verified
    assert [n.attempt for n in db.notes("run-1", "W-000001")] == [1, 2]
    assert db.window("run-1", "W-999999") is None


def test_rewriting_a_window_keeps_its_notes(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="run-1", status="running", source="live"))
    db.put_windows("run-1", [window("W-000001", 0)])
    db.put_notes("run-1", [note("W-000001", 1, True)])

    db.put_windows("run-1", [window("W-000001", 0, look_first="feedback_pos_0")])
    stored = db.window("run-1", "W-000001")

    assert stored is not None
    assert stored.window.look_first == "feedback_pos_0"
    assert stored.verified


def test_window_filters_sorts_and_pages(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="run-1", status="done", source="live"))
    db.put_windows(
        "run-1",
        [
            window("W-000001", 0, score=0.5, flagged=False, max_abs_z=9.0),
            window("W-000002", 1, score=0.97, flagged=True, max_abs_z=1.0),
            window("W-000003", 2, line="FactoryWave", max_abs_z=4.0),
            window("W-000004", 3, score=0.99, flagged=True),
        ],
    )

    def ids(**query: Any) -> list[str]:
        return [w.window.window_id for w in db.windows("run-1", **query)]

    assert ids() == ["W-000001", "W-000002", "W-000003", "W-000004"]
    assert ids(sort="score") == ["W-000004", "W-000002", "W-000001", "W-000003"]
    assert ids(sort="deviation") == ["W-000001", "W-000003", "W-000002", "W-000004"]
    assert ids(line="FactoryWave") == ["W-000003"]
    assert ids(flagged=True) == ["W-000002", "W-000004"]
    assert ids(offset=1, limit=2) == ["W-000002", "W-000003"]
    assert db.count_windows("run-1") == 4
    assert db.count_windows("run-1", line="AURSAD", flagged=False) == 1


def test_report_round_trip_keeps_line_order(db: NightDb) -> None:
    db.create_run(RunRecord(run_id="run-1", status="done", source="live"))
    item = ReportItem(
        window_id="W-000001", task="aursad", score=1.0, look_first="x", note="n", verified=True
    )
    lines = [
        ReportLine(
            line="AURSAD",
            verdict="screening",
            label="Abnormal-behaviour screening only",
            scanned=4,
            flagged=1,
            threshold=0.9,
            items=[item],
        ),
        ReportLine(
            line="FactoryWave",
            verdict="closed",
            label="Facts, not diagnosis",
            scanned=3,
            flagged=None,
            threshold=None,
            items=[],
        ),
    ]

    db.put_report("run-1", lines)
    db.put_report("run-1", lines)

    assert db.report("run-1") == lines
    assert db.report("run-2") == []


def test_replace_run_drops_the_earlier_copy(db: NightDb) -> None:
    run = RunRecord(run_id="run-1", status="done", source="import")
    db.replace_run(run, [window("W-000001", 0), window("W-000002", 1)], [note("W-000001", 1, True)])

    db.replace_run(run, [window("W-000003", 0)], [])

    assert [w.window.window_id for w in db.windows("run-1")] == ["W-000003"]
    assert db.count_notes("run-1") == 0
