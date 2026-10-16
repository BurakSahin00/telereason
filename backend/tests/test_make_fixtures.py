"""scripts/make_fixtures.py: fixtures from a night run in the store, equal to the API's answers."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from scripts.make_fixtures import main
from telereason.ai.engine import FakeEngine
from telereason.api.main import create_app
from telereason.api.schemas import GateLine, GateResponse, StepTimings, WindowDetail
from telereason.night.report import write_report
from telereason.settings import DEFAULT_MODEL
from telereason.store.db import NightDb, NoteRecord, RunRecord, WindowRecord
from tests.test_db_night import db_settings
from tests.test_fixtures import (
    FIXTURES,
    JSON_FIXTURES,
    SSE_EVENTS,
    assert_round_trip,
    load,
    parse_sse,
)
from tests.test_lifespan import wait_until_ready
from tests.test_report import fixture_night

OLD_RUN = "run-20261015-1"
NEW_RUN = "run-20261016-1"
# Files a night run does not store; the script keeps them as they are.
KEPT = ("health.json", "examples.json", "night_run_started.json")


def add_run(db: NightDb, run_id: str, finished_at: str, status: str = "done") -> None:
    """A finished live run: a flagged and an unflagged AURSAD window, one FactoryWave window."""
    gate = GateResponse(
        run_id=run_id,
        lines=[
            GateLine(
                line=line,
                verdict=verdict,
                label=verdict,
                auroc=auroc,
                auroc_ci=(auroc - 0.1, auroc + 0.1),
                explanation="",
            )
            for line, verdict, auroc in (
                ("FactoryWave", "closed", 0.52),
                ("AURSAD", "screening", 0.91),
            )
        ],
    )
    windows = [
        WindowRecord("W-100001", "AURSAD", "aursad", 0, score=0.97, flagged=True, max_abs_z=3.0),
        WindowRecord("W-100002", "AURSAD", "aursad", 1, score=0.2, flagged=False, max_abs_z=1.0),
        WindowRecord("W-100003", "FactoryWave", "screwing", 2, max_abs_z=4.0),
    ]
    notes = [
        NoteRecord("W-100001", 1, "**Score 0.97.**", "Score 0.97.", True),
        NoteRecord("W-100002", 1, "Score 0.2.", "Score 0.2.", True),
        # Failed the verifier: the facts stand alone.
        NoteRecord("W-100003", 1, "Unverified.", "Unverified.", False),
    ]
    db.init().replace_run(
        RunRecord(
            run_id=run_id,
            status=status,  # type: ignore[arg-type]
            source="live",
            step="report",
            done=len(windows),
            total=len(windows),
            finished_at=finished_at,
            step_timings=StepTimings(gate=1.0, facts=2.0, scan=3.0, report=0.5),
            total_s=7.0,
            gate=gate,
        ),
        windows,
        notes,
    )
    write_report(db, run_id)


@pytest.fixture
def db(tmp_path: Path) -> NightDb:
    db = NightDb(tmp_path / "night.sqlite")
    add_run(db, OLD_RUN, "2026-10-15T09:00:00+00:00")
    add_run(db, NEW_RUN, "2026-10-16T09:30:00+00:00")
    return db


@pytest.fixture
def out(tmp_path: Path) -> Path:
    out = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, out)
    return out


def read(out: Path, name: str) -> Any:
    return json.loads((out / name).read_text(encoding="utf-8"))


def make(db: NightDb, out: Path, *run: str) -> None:
    main(["--db", str(db.path), "--out", str(out), *(["--run", *run] if run else [])])


def test_fixtures_match_the_schemas(db: NightDb, out: Path) -> None:
    make(db, out)

    for name, model in JSON_FIXTURES.items():
        assert_round_trip(model, read(out, name))
    for window_id, window in read(out, "window_detail.json").items():
        assert_round_trip(WindowDetail, window)
        assert window["window_id"] == window_id
    for event, data in parse_sse((out / "ask.sse").read_text(encoding="utf-8")):
        assert_round_trip(SSE_EVENTS[event], data)


def test_fixtures_are_the_api_answers_for_the_latest_run(
    tmp_path: Path, db: NightDb, out: Path
) -> None:
    make(db, out)
    app = create_app(db_settings(tmp_path), engine=FakeEngine(model=DEFAULT_MODEL))

    with TestClient(app) as client:
        wait_until_ready(client)
        answers = {
            "report_latest.json": client.get("/api/report/latest").json(),
            "gate.json": client.get("/api/gate").json(),
            "windows.json": client.get("/api/windows", params={"page_size": 20}).json(),
            "night_run.json": client.get(f"/api/night-runs/{NEW_RUN}").json(),
        }
        details = read(out, "window_detail.json")
        windows = {wid: client.get(f"/api/windows/{wid}").json() for wid in details}
        last_night = client.get("/api/system").json()["last_night"]

    for name, answer in answers.items():
        assert read(out, name) == answer, name
    assert details == windows
    assert read(out, "system.json")["last_night"] == last_night
    # Report windows (the ask window among them), then the unflagged AURSAD window.
    assert list(details) == ["W-100001", "W-100003", "W-100002"]
    assert details["W-100003"]["note"] == ""


def test_ask_sample_opens_the_closed_lines_first_report_window(db: NightDb, out: Path) -> None:
    make(db, out)

    messages = dict(parse_sse((out / "ask.sse").read_text(encoding="utf-8")))

    assert read(out, "ask_request.json")["window_id"] == "W-100003"
    assert messages["tool_call"]["args"] == {"window_id": "W-100003"}
    assert "(AUROC 0.520, interval 0.420 to 0.620)" in messages["answer"]["text"]


def test_values_a_run_does_not_store_are_kept(db: NightDb, out: Path) -> None:
    make(db, out)

    system, before = read(out, "system.json"), load("system.json")
    done = dict(parse_sse((out / "ask.sse").read_text(encoding="utf-8")))["done"]

    assert (system["gpu"], system["versions"]) == (before["gpu"], before["versions"])
    assert done == dict(parse_sse((FIXTURES / "ask.sse").read_text(encoding="utf-8")))["done"]
    for name in KEPT:
        assert (out / name).read_bytes() == (FIXTURES / name).read_bytes()


def test_readme_names_the_run_its_date_and_source(db: NightDb, out: Path) -> None:
    make(db, out)

    readme = (out / "README.md").read_text(encoding="utf-8")

    assert f"- Run: `{NEW_RUN}`" in readme
    assert "- Date: 2026-10-16" in readme
    assert "- Source: store `night.sqlite`; the run was run by the app" in readme
    assert "holds 3 windows" in readme
    assert "1 flagged AURSAD, 1 unflagged AURSAD, 1 FactoryWave" in readme


def test_run_option_picks_an_earlier_run(db: NightDb, out: Path) -> None:
    make(db, out, OLD_RUN)

    assert read(out, "report_latest.json")["run_id"] == OLD_RUN
    assert read(out, "night_run.json")["run_id"] == OLD_RUN
    assert read(out, "system.json")["last_night"]["run_id"] == OLD_RUN
    assert f"- Run: `{OLD_RUN}`" in (out / "README.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("run_id", ["run-20261017-1", "run-20261017-2"])
def test_unknown_or_unfinished_run_stops(db: NightDb, out: Path, run_id: str) -> None:
    add_run(db, "run-20261017-2", "", status="running")

    with pytest.raises(SystemExit, match=run_id):
        make(db, out, run_id)
    assert read(out, "report_latest.json") == load("report_latest.json")


def test_missing_store_stops(tmp_path: Path, out: Path) -> None:
    with pytest.raises(SystemExit, match="does not exist"):
        make(NightDb(tmp_path / "absent.sqlite"), out)
    assert not (tmp_path / "absent.sqlite").exists()


def test_stored_11_oct_night_gives_back_the_fixtures(tmp_path: Path, out: Path) -> None:
    db = NightDb(tmp_path / "night.sqlite")
    fixture_night(db)

    make(db, out)

    # The rebuilt night keeps the fixture windows but not the night's order of the others.
    changed = {"windows.json", "README.md"}
    for path in FIXTURES.iterdir():
        if path.name not in changed:
            assert (out / path.name).read_bytes() == path.read_bytes(), path.name
