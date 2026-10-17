import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telereason.api.main import create_app
from telereason.api.remote import KEY_HEADER, RemoteBackend

LIVE_URL = "http://live.example"
KEY = "demo-key"
LIVE_HEALTH = {"status": "ok", "engine": "vllm", "model": "openai/gpt-oss-120b", "ready": True}
LIVE_SSE = (
    'event: answer\ndata: {"answer": "ANSWERED", "text": "live"}\n\n'
    'event: done\ndata: {"total_ms": 1880, "tokens": 97}\n\n'
)


class Live:
    """A stand-in for the live backend that records the requests it gets."""

    def __init__(
        self,
        health: dict[str, object] | None = None,
        key: str = KEY,
        fail: tuple[str, ...] = (),
    ) -> None:
        self.health = LIVE_HEALTH if health is None else health
        self.key = key
        self.fail = fail
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.headers.get(KEY_HEADER) != self.key:
            return httpx.Response(401, json={"error": {"code": "bad_request", "message": "key"}})
        path = request.url.path
        if path in self.fail:
            raise httpx.ConnectError("live backend is down", request=request)
        if path == "/api/health":
            return httpx.Response(200, json=self.health)
        if path == "/api/ask":
            return httpx.Response(
                200, headers={"content-type": "text/event-stream; charset=utf-8"}, content=LIVE_SSE
            )
        return httpx.Response(
            200,
            headers={"access-control-allow-origin": "*"},
            json={"live": path, "query": request.url.query.decode()},
        )

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


def demo(monkeypatch: pytest.MonkeyPatch, live: Live, **env: str) -> FastAPI:
    """The fake-mode demo with REMOTE_API_URL pointing at `live`."""
    monkeypatch.setenv("ENGINE", "fake")
    monkeypatch.setenv("REMOTE_API_URL", LIVE_URL + "/")
    monkeypatch.setenv("REMOTE_API_KEY", KEY)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    app = create_app()
    app.state.remote.client = RemoteBackend(
        LIVE_URL, KEY, transport=httpx.MockTransport(live)
    ).client
    return app


def test_demo_answers_from_the_live_backend_while_it_is_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = Live()
    with TestClient(demo(monkeypatch, live)) as client:
        health = client.get("/api/health")
        gate = client.get(
            "/api/gate?line=AURSAD", headers={"Origin": "https://x.example", "Cookie": "a=b"}
        )

    assert health.json() == LIVE_HEALTH
    assert gate.json() == {"live": "/api/gate", "query": "line=AURSAD"}
    assert all(request.headers[KEY_HEADER] == KEY for request in live.requests)
    forwarded = live.requests[-1].headers
    assert "origin" not in forwarded and "cookie" not in forwarded


def test_demo_streams_live_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    live = Live()
    with TestClient(demo(monkeypatch, live)) as client:
        response = client.post("/api/ask", json={"question": "Which line needs attention first?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.text == LIVE_SSE
    asked = live.requests[-1]
    assert json.loads(asked.content) == {"question": "Which line needs attention first?"}


def test_demo_answers_locally_while_the_live_engine_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = Live(health={**LIVE_HEALTH, "ready": False})
    with TestClient(demo(monkeypatch, live)) as client:
        health = client.get("/api/health")
        gate = client.get("/api/gate")

    assert health.json()["engine"] == "fake"
    assert "lines" in gate.json()
    # One health check covers both requests.
    assert live.paths() == ["/api/health"]


def test_demo_answers_locally_when_the_live_backend_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = Live(fail=("/api/health",))
    with TestClient(demo(monkeypatch, live)) as client:
        response = client.get("/api/health")

    assert response.json()["engine"] == "fake"


def test_demo_answers_locally_when_a_forwarded_request_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = Live(fail=("/api/gate",))
    app = demo(monkeypatch, live)
    with TestClient(app) as client:
        gate = client.get("/api/gate")
        health = client.get("/api/health")

    assert "lines" in gate.json()
    # Marked down until the next check, so the next request is answered here too.
    assert app.state.remote.ready is False
    assert health.json()["engine"] == "fake"


def test_demo_answers_locally_when_the_key_does_not_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    live = Live(key="another-key")
    with TestClient(demo(monkeypatch, live)) as client:
        response = client.get("/api/health")

    assert response.json()["engine"] == "fake"


def test_demo_rate_limits_forwarded_questions(monkeypatch: pytest.MonkeyPatch) -> None:
    live = Live()
    app = demo(monkeypatch, live, RATE_LIMIT_ASK_PER_MIN="1")
    with TestClient(app) as client:
        first = client.post("/api/ask", json={"question": "Which line?"})
        second = client.post("/api/ask", json={"question": "Which line?"})

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"
    assert "Retry-After" in second.headers
    assert live.paths().count("/api/ask") == 1


def test_cors_covers_live_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    live = Live()
    app = demo(monkeypatch, live, CORS_ORIGINS="https://demo.vercel.app")
    with TestClient(app) as client:
        response = client.get("/api/gate", headers={"Origin": "https://demo.vercel.app"})

    assert response.json()["live"] == "/api/gate"
    # This app's own origin, not the live backend's "*".
    assert response.headers.get_list("access-control-allow-origin") == ["https://demo.vercel.app"]


def test_no_proxy_without_a_remote_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMOTE_API_URL", raising=False)

    assert create_app().state.remote is None


def test_no_proxy_in_real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REMOTE_API_URL", LIVE_URL)
    monkeypatch.setenv("ENGINE", "vllm")

    assert create_app().state.remote is None


def test_live_backend_requires_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMOTE_API_URL", raising=False)
    monkeypatch.setenv("API_KEY", KEY)
    client = TestClient(create_app())

    without = client.get("/api/health")
    wrong = client.get("/api/health", headers={KEY_HEADER: "wrong"})
    with_key = client.get("/api/health", headers={KEY_HEADER: KEY})
    docs = client.get("/docs")

    assert without.status_code == 401
    assert without.json()["error"]["code"] == "bad_request"
    assert wrong.status_code == 401
    assert with_key.status_code == 200
    assert docs.status_code == 200
