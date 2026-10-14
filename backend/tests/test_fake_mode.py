import time

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel

from telereason.api import fake
from telereason.api.main import create_app
from telereason.api.schemas import (
    ExamplesResponse,
    GateResponse,
    NightRunProgress,
    NightRunStarted,
    ReportResponse,
    SystemResponse,
)
from telereason.settings import DEFAULT_MODEL, EngineKind, Settings
from tests.test_contract import assert_error_shape
from tests.test_fixtures import SSE_EVENTS, assert_round_trip, load, parse_sse

# (method, path, expected status, response model, fixture served in fake mode)
FIXTURE_ENDPOINTS: list[tuple[str, str, int, type[BaseModel], str]] = [
    ("GET", "/api/report/latest", 200, ReportResponse, "report_latest.json"),
    ("GET", "/api/gate", 200, GateResponse, "gate.json"),
    ("GET", "/api/system", 200, SystemResponse, "system.json"),
    ("GET", "/api/examples", 200, ExamplesResponse, "examples.json"),
    ("POST", "/api/night-runs", 202, NightRunStarted, "night_run_started.json"),
    ("GET", "/api/night-runs/run-20261015-1", 200, NightRunProgress, "night_run.json"),
]
ENDPOINT_IDS = [f"{method} {path}" for method, path, *_ in FIXTURE_ENDPOINTS]

ASK_BODY = load("ask_request.json")

REPORT_WINDOW_IDS = [
    item["window_id"] for line in load("report_latest.json")["lines"] for item in line["items"]
]


def make_client(engine: EngineKind) -> TestClient:
    return TestClient(create_app(Settings(engine=engine, model=DEFAULT_MODEL)))


@pytest.fixture
def fake_client() -> TestClient:
    return make_client("fake")


@pytest.fixture
def vllm_client() -> TestClient:
    return make_client("vllm")


@pytest.fixture
def no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fake, "EVENT_DELAY_S", (0.0, 0.0))


@pytest.mark.parametrize(
    ("method", "path", "status", "model", "name"), FIXTURE_ENDPOINTS, ids=ENDPOINT_IDS
)
def test_fake_mode_serves_fixture(
    fake_client: TestClient,
    method: str,
    path: str,
    status: int,
    model: type[BaseModel],
    name: str,
) -> None:
    response = fake_client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == status
    assert_round_trip(model, response.json())
    assert response.json() == load(name)


# Real mode serves the stored night (report, gate, examples; see test_stored_night.py).
NOT_IN_STORED_NIGHT = [
    (method, path) for method, path, *_ in FIXTURE_ENDPOINTS if "night-runs" in path
] + [("GET", "/api/system")]


@pytest.mark.parametrize(("method", "path"), NOT_IN_STORED_NIGHT)
def test_vllm_mode_is_not_implemented(vllm_client: TestClient, method: str, path: str) -> None:
    response = vllm_client.request(method, path, json={} if method == "POST" else None)

    assert response.status_code == 501
    assert_error_shape(response.json(), "not_implemented")


@pytest.mark.parametrize("window_id", REPORT_WINDOW_IDS)
def test_fake_mode_opens_every_report_window(fake_client: TestClient, window_id: str) -> None:
    response = fake_client.get(f"/api/windows/{window_id}")

    assert response.status_code == 200
    assert response.json() == load("window_detail.json")[window_id]


def test_health_reports_fake_engine_not_fixture(fake_client: TestClient) -> None:
    # fixtures/health.json says "vllm"; serving it would hide fake mode in the UI.
    assert load("health.json")["engine"] == "vllm"

    response = fake_client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["engine"] == "fake"


def test_fake_night_run_validates_body(fake_client: TestClient) -> None:
    response = fake_client.post("/api/night-runs", json={"max_windows": 0})

    assert response.status_code == 422
    assert_error_shape(response.json(), "bad_request")


@pytest.mark.usefixtures("no_delay")
def test_fake_ask_streams_events_in_order(fake_client: TestClient) -> None:
    response = fake_client.post("/api/ask", json=ASK_BODY)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    messages = parse_sse(response.text)
    assert [event for event, _ in messages] == ["tool_call", "tool_result", "answer", "done"]
    for event, data in messages:
        assert_round_trip(SSE_EVENTS[event], data)


@pytest.mark.usefixtures("no_delay")
def test_fake_ask_accepts_question_without_window(fake_client: TestClient) -> None:
    response = fake_client.post("/api/ask", json={"question": ASK_BODY["question"]})

    assert response.status_code == 200


@pytest.mark.parametrize(
    "body",
    [{}, {"question": ""}, {"window_id": "W-246062"}, {"question": 1}],
    ids=["empty", "blank question", "no question", "question not a string"],
)
def test_fake_ask_rejects_invalid_body(fake_client: TestClient, body: dict[str, object]) -> None:
    response = fake_client.post("/api/ask", json=body)

    assert response.status_code == 422
    assert_error_shape(response.json(), "bad_request")


def test_event_delay_stays_in_range() -> None:
    delays = [fake.event_delay_s() for _ in range(200)]

    assert all(0.3 <= delay <= 0.8 for delay in delays)


def test_fake_ask_pauses_between_events(
    fake_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fake, "EVENT_DELAY_S", (0.01, 0.01))

    started = time.perf_counter()
    response = fake_client.post("/api/ask", json=ASK_BODY)

    # Four events, so three pauses.
    assert len(parse_sse(response.text)) == 4
    assert time.perf_counter() - started >= 0.03
