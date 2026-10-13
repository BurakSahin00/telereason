import json

import pytest
from fastapi.testclient import TestClient

from telereason.api.export_openapi import OPENAPI_PATH, build_openapi
from telereason.api.main import create_app
from telereason.settings import DEFAULT_MODEL, Settings

EXPECTED_PATHS = {
    "/api/health",
    "/api/system",
    "/api/gate",
    "/api/report/latest",
    "/api/windows",
    "/api/windows/{window_id}",
    "/api/windows/{window_id}/signals",
    "/api/ask",
    "/api/examples",
    "/api/night-runs",
    "/api/night-runs/{run_id}",
}

SSE_PAYLOADS = {"ToolCallEvent", "ToolResultEvent", "AnswerEvent", "DoneEvent", "ErrorResponse"}


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Settings(engine="fake", model=DEFAULT_MODEL)))


def assert_error_shape(body: object, code: str) -> None:
    assert isinstance(body, dict)
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_openapi_lists_all_eleven_paths() -> None:
    assert set(build_openapi()["paths"]) == EXPECTED_PATHS


def test_openapi_has_sse_payload_models() -> None:
    schemas = build_openapi()["components"]["schemas"]
    assert SSE_PAYLOADS <= set(schemas)
    assert "HTTPValidationError" not in schemas


def test_docs_openapi_json_is_current() -> None:
    committed = json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))
    assert committed == build_openapi(), (
        "docs/openapi.json is stale; run `uv run python -m telereason.api.export_openapi`"
    )


def test_unknown_path_returns_error_shape(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert_error_shape(response.json(), "not_found")


def test_invalid_query_returns_error_shape(client: TestClient) -> None:
    response = client.get("/api/windows", params={"page_size": 500})
    assert response.status_code == 422
    assert_error_shape(response.json(), "bad_request")
    assert "page_size" in response.json()["error"]["message"]


def test_invalid_body_returns_error_shape(client: TestClient) -> None:
    response = client.post("/api/ask", json={"question": ""})
    assert response.status_code == 422
    assert_error_shape(response.json(), "bad_request")


def test_stub_returns_not_implemented() -> None:
    # Fake mode serves fixtures; the real endpoints are still stubs.
    client = TestClient(create_app(Settings(engine="vllm", model=DEFAULT_MODEL)))
    response = client.get("/api/gate")
    assert response.status_code == 501
    assert_error_shape(response.json(), "not_implemented")
