"""ENGINE=vllm with NIGHT_SOURCE=db: the endpoints serve the run that finished last (#39)."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from telereason.ai.engine import FakeEngine
from telereason.api.ask import agent_data
from telereason.api.main import create_app
from telereason.api.schemas import FeatureContribution, GateLine, GateResponse, StepTimings
from telereason.night.report import write_report
from telereason.settings import DEFAULT_MODEL, EngineKind, Settings
from telereason.store.db import NightDb, NoteRecord, RunRecord, WindowRecord
from tests.test_contract import assert_error_shape
from tests.test_fixtures import load
from tests.test_lifespan import wait_until_ready
from tests.test_report import fixture_night

REPORT_WINDOW_IDS = [
    item["window_id"] for line in load("report_latest.json")["lines"] for item in line["items"]
]
# Later than the fixture night, so it must have another id.
NEW_RUN = "run-20261017-1"


def db_settings(tmp_path: Path, engine: EngineKind = "vllm") -> Settings:
    return Settings(
        engine=engine,
        model=DEFAULT_MODEL,
        data_dir=str(tmp_path),
        night_source="db",
        db_path=str(tmp_path / "night.sqlite"),
    )


@pytest.fixture
def db(tmp_path: Path) -> NightDb:
    db = NightDb(tmp_path / "night.sqlite")
    fixture_night(db)
    return db


@pytest.fixture
def app(tmp_path: Path, db: NightDb) -> FastAPI:
    return create_app(db_settings(tmp_path), engine=FakeEngine(model=DEFAULT_MODEL))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        wait_until_ready(client)
        yield client


def add_newer_run(db: NightDb) -> None:
    """A small finished run after the fixture night: one AURSAD window, one FactoryWave window."""
    gate = GateResponse(
        run_id=NEW_RUN,
        lines=[
            GateLine(
                line=line,
                verdict=verdict,
                label="label",
                auroc=0.5,
                auroc_ci=(0.4, 0.6),
                explanation="explanation",
            )
            for line, verdict in (("FactoryWave", "closed"), ("AURSAD", "screening"))
        ],
    )
    contribution = (FeatureContribution(feature="effort_current_0.mean", contribution=0.3),)
    scored = {"score": 0.97, "flagged": True, "max_abs_z": 3.0, "top_features": contribution}
    windows = [
        WindowRecord("W-100001", "AURSAD", "aursad", 0, **scored),
        # A score stored on a closed line must still not reach the API.
        WindowRecord("W-100002", "FactoryWave", "screwing", 1, **scored),
    ]
    notes = [
        NoteRecord("W-100001", 1, "first", "first", False),
        NoteRecord("W-100001", 2, "retry", "retry", False),
        NoteRecord("W-100002", 1, "**facts**", "facts", True),
    ]
    db.replace_run(
        RunRecord(
            run_id=NEW_RUN,
            status="done",
            source="live",
            step="report",
            done=2,
            total=2,
            finished_at="2026-10-17T09:30:00+00:00",
            step_timings=StepTimings(gate=1.0, facts=2.0, scan=3.0, report=0.5),
            total_s=7.0,
            gate=gate,
        ),
        windows,
        notes,
    )
    write_report(db, NEW_RUN)


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/api/report/latest", "report_latest.json"),
        ("/api/gate", "gate.json"),
        ("/api/examples", "examples.json"),
    ],
)
def test_stored_run_serves_the_fixture_responses(client: TestClient, path: str, name: str) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == load(name)


def test_system_reports_the_runs_timings(client: TestClient) -> None:
    response = client.get("/api/system")

    assert response.status_code == 200
    assert response.json()["last_night"] == load("system.json")["last_night"]


@pytest.mark.parametrize("window_id", REPORT_WINDOW_IDS)
def test_every_report_window_opens(client: TestClient, window_id: str) -> None:
    response = client.get(f"/api/windows/{window_id}")

    assert response.status_code == 200
    assert response.json() == load("window_detail.json")[window_id]


def test_window_list_pages_through_the_whole_night(client: TestClient) -> None:
    first = client.get("/api/windows", params={"page_size": 20}).json()
    flagged = client.get("/api/windows", params={"line": "AURSAD", "flagged": "true"}).json()
    by_score = client.get("/api/windows", params={"sort": "score", "page_size": 1}).json()
    closed = client.get("/api/windows", params={"line": "FactoryWave"}).json()

    assert first["total"] == 7422
    assert first["items"][:10] == [
        {k: v for k, v in d.items() if k in first["items"][0]}
        for d in list(load("window_detail.json").values())[:10]
    ]
    assert flagged["total"] == load("report_latest.json")["lines"][0]["flagged"]
    assert (
        by_score["items"][0]["score"] == load("report_latest.json")["lines"][0]["items"][0]["score"]
    )
    assert closed["total"] == 3354
    assert {(i["score"], i["flagged"]) for i in closed["items"]} == {(None, None)}


def test_unknown_window_is_404(client: TestClient) -> None:
    response = client.get("/api/windows/W-999999")

    assert response.status_code == 404
    assert_error_shape(response.json(), "not_found")


def test_the_run_that_finished_last_is_served(client: TestClient, db: NightDb) -> None:
    add_newer_run(db)

    report = client.get("/api/report/latest").json()
    gate = client.get("/api/gate").json()
    system = client.get("/api/system").json()
    windows = client.get("/api/windows").json()

    assert report["run_id"] == gate["run_id"] == system["last_night"]["run_id"] == NEW_RUN
    assert report["timings_s"] == {"gate": 1, "facts": 2, "scan": 3, "report": 0.5, "total": 7}
    assert [(line["line"], line["scanned"]) for line in report["lines"]] == [
        ("AURSAD", 1),
        ("FactoryWave", 1),
    ]
    assert windows["total"] == 2
    assert client.get(f"/api/windows/{REPORT_WINDOW_IDS[0]}").status_code == 404


def test_unverified_note_is_not_shown(client: TestClient, db: NightDb) -> None:
    add_newer_run(db)

    detail = client.get("/api/windows/W-100001").json()
    item = client.get("/api/report/latest").json()["lines"][0]["items"][0]

    assert (detail["note"], detail["verified"]) == ("", False)
    assert (item["note"], item["verified"]) == ("", False)
    assert detail["top_features"] == [{"feature": "effort_current_0.mean", "contribution": 0.3}]


def test_closed_line_window_has_no_score_or_contributions(client: TestClient, db: NightDb) -> None:
    add_newer_run(db)

    detail = client.get("/api/windows/W-100002").json()

    assert detail["verdict"] == "closed"
    assert (detail["score"], detail["flagged"], detail["top_features"]) == (None, None, [])
    assert detail["note"] == "facts"


def test_agent_reads_the_run_that_finished_last(app: FastAPI, db: NightDb) -> None:
    request = Request({"type": "http", "app": app})

    stored = agent_data(request)
    again = agent_data(request)
    add_newer_run(db)
    newer = agent_data(request)

    assert again is stored
    for window_id in REPORT_WINDOW_IDS:
        expected = load("window_detail.json")[window_id]
        assert stored.store.get_window(window_id).model_dump(mode="json") == expected
    assert (
        stored.report is not None and stored.report.run_id == load("report_latest.json")["run_id"]
    )
    assert newer.gate.run_id == NEW_RUN
    assert newer.store.get_window("W-100001").verdict == "screening"


@pytest.mark.parametrize("path", ["/api/report/latest", "/api/gate", "/api/windows"])
def test_empty_store_is_not_ready(tmp_path: Path, path: str) -> None:
    app = create_app(db_settings(tmp_path), engine=FakeEngine(model=DEFAULT_MODEL))

    with TestClient(app) as client:
        response = client.get(path)
        system = client.get("/api/system").json()

    assert response.status_code == 503
    assert_error_shape(response.json(), "engine_not_ready")
    assert system["last_night"] is None
    # Reading an absent store does not create the file.
    assert not (tmp_path / "night.sqlite").exists()


def test_fake_mode_keeps_serving_fixtures(tmp_path: Path, db: NightDb) -> None:
    add_newer_run(db)
    app = create_app(db_settings(tmp_path, engine="fake"))

    with TestClient(app) as client:
        report = client.get("/api/report/latest").json()

    assert report == load("report_latest.json")
