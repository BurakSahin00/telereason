"""ENGINE=vllm with NIGHT_SOURCE=stored: the stored 11 Oct night is served on the droplet."""

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.requests import Request

from telereason.ai.engine import FakeEngine
from telereason.api.ask import agent_data
from telereason.api.main import create_app
from telereason.data.paths import factorywave_dir
from telereason.settings import DEFAULT_MODEL, Settings
from tests.test_contract import assert_error_shape
from tests.test_data_loader import write_factorywave
from tests.test_fixtures import load
from tests.test_lifespan import wait_until_ready

REPORT_WINDOW_IDS = [
    item["window_id"] for line in load("report_latest.json")["lines"] for item in line["items"]
]


@pytest.fixture
def app(tmp_path: Path) -> FastAPI:
    # Downloaded data, as on the droplet: the stored night must still win over it.
    write_factorywave(factorywave_dir(tmp_path))
    settings = Settings(engine="vllm", model=DEFAULT_MODEL, data_dir=str(tmp_path))
    return create_app(settings, engine=FakeEngine(model=DEFAULT_MODEL))


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        wait_until_ready(client)
        yield client


@pytest.mark.parametrize(
    ("path", "name"),
    [
        ("/api/report/latest", "report_latest.json"),
        ("/api/gate", "gate.json"),
        ("/api/examples", "examples.json"),
    ],
)
def test_stored_night_endpoints_serve_the_11_oct_files(
    client: TestClient, path: str, name: str
) -> None:
    response = client.get(path)

    assert response.status_code == 200
    assert response.json() == load(name)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/night-runs"),
        ("GET", "/api/night-runs/run-20261015-1"),
    ],
)
def test_endpoints_without_a_stored_night_stay_not_implemented(
    client: TestClient, method: str, path: str
) -> None:
    response = client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == 501
    assert_error_shape(response.json(), "not_implemented")


def test_window_list_is_the_stored_night(client: TestClient) -> None:
    response = client.get("/api/windows")

    assert response.status_code == 200
    stored_ids = {item["window_id"] for item in load("windows.json")["items"]}
    assert {item["window_id"] for item in response.json()["items"]} <= stored_ids


def test_report_has_twenty_windows() -> None:
    assert len(REPORT_WINDOW_IDS) == 20


@pytest.mark.parametrize("window_id", REPORT_WINDOW_IDS)
def test_every_report_window_opens(client: TestClient, window_id: str) -> None:
    response = client.get(f"/api/windows/{window_id}")

    assert response.status_code == 200
    assert response.json() == load("window_detail.json")[window_id]


def test_agent_store_opens_every_report_window(app: FastAPI) -> None:
    store = agent_data(Request({"type": "http", "app": app})).store

    for window_id in REPORT_WINDOW_IDS:
        expected = load("window_detail.json")[window_id]
        assert store.get_window(window_id).model_dump(mode="json") == expected


def test_signals_still_read_the_downloaded_data(client: TestClient) -> None:
    response = client.get("/api/windows/episode-1/signals")

    assert response.status_code == 503
    assert "normal band not computed yet" in response.json()["error"]["message"]
