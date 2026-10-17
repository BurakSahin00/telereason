"""scripts/notes_summary.py: note counts of one run, read from a store built with NightDb."""

import json
from pathlib import Path

import pytest

from scripts.notes_summary import main
from telereason.store.db import NightDb, NoteRecord, RunRecord, WindowRecord

RUN = "run-20261016-1"
OTHER = "run-20261015-1"
SECRET_TEXT = "Note text that must not reach the summary."


def note(window_id: str, attempt: int, passed: bool, tokens: int | None) -> NoteRecord:
    return NoteRecord(
        window_id, attempt, SECRET_TEXT, SECRET_TEXT, passed, completion_tokens=tokens
    )


@pytest.fixture
def db(tmp_path: Path) -> NightDb:
    db = NightDb(tmp_path / "night.sqlite").init()
    windows = [WindowRecord(f"W-00000{n}", "AURSAD", "aursad", n) for n in range(1, 5)]
    notes = [
        note("W-000001", 1, True, 200),  # passed on the first attempt
        note("W-000002", 1, False, 220),  # passed on the retry
        note("W-000002", 2, True, 240),
        note("W-000003", 1, False, None),  # failed twice: facts only
        note("W-000003", 2, False, 260),
        # W-000004 has no note.
    ]
    db.replace_run(RunRecord(RUN, "done", "live"), windows, notes)
    # Another run in the same store must not be counted.
    db.replace_run(
        RunRecord(OTHER, "done", "live"),
        [WindowRecord("W-000001", "AURSAD", "aursad", 0)],
        [note("W-000001", 1, True, 999)],
    )
    return db


def run(db: NightDb, out: Path, run_id: str = RUN) -> None:
    main(["--db", str(db.path), "--run", run_id, "--out", str(out)])


def test_counts_the_notes_of_one_run(db: NightDb, tmp_path: Path) -> None:
    out = tmp_path / "results" / "notes.json"

    run(db, out)

    assert json.loads(out.read_text(encoding="utf-8")) == {
        "run_id": RUN,
        "windows": 4,
        "notes": 3,
        "windows_without_note": 1,
        "note_attempts": 5,
        "verified": 2,
        "passed_first_attempt": 1,
        "passed_second_attempt": 1,
        "completion_tokens_counted": 4,
        "mean_completion_tokens": 230.0,
    }
    assert SECRET_TEXT not in out.read_text(encoding="utf-8")


def test_store_is_only_read(db: NightDb, tmp_path: Path) -> None:
    before = db.path.read_bytes()

    run(db, tmp_path / "notes.json")

    assert db.path.read_bytes() == before


def test_unknown_run_stops(db: NightDb, tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="no night run run-20261017-1"):
        run(db, tmp_path / "notes.json", "run-20261017-1")
    assert not (tmp_path / "notes.json").exists()


def test_missing_store_stops_without_creating_it(tmp_path: Path) -> None:
    absent = NightDb(tmp_path / "absent.sqlite")

    with pytest.raises(SystemExit, match="does not exist"):
        run(absent, tmp_path / "notes.json")
    assert not absent.path.exists()
