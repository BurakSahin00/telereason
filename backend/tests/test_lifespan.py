import asyncio
import sys
import threading
import time

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from telereason.ai.engine import WARMUP_MAX_TOKENS, FakeEngine, Priority
from telereason.api.main import create_app, open_engine
from telereason.settings import DEFAULT_MODEL, Settings
from tests.test_contract import assert_error_shape

VLLM_SETTINGS = Settings(engine="vllm", model=DEFAULT_MODEL)


class SlowEngine(FakeEngine):
    """Fake engine whose start waits until the test releases it, like a loading model."""

    def __init__(self) -> None:
        super().__init__(model=DEFAULT_MODEL)
        self.release = threading.Event()

    async def start(self) -> None:
        await asyncio.to_thread(self.release.wait, 10)
        await super().start()


class BrokenEngine(FakeEngine):
    async def start(self) -> None:
        raise RuntimeError("out of GPU memory")


def wait_until_ready(client: TestClient, timeout_s: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get("/api/health").json()
        if body["ready"]:
            return body
        time.sleep(0.01)
    raise AssertionError("engine did not become ready")


def test_lifespan_opens_warms_up_and_closes_the_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "vllm", None)  # any GPU import would raise ImportError
    engine = FakeEngine(model=DEFAULT_MODEL)
    app = create_app(Settings(engine="fake", model=DEFAULT_MODEL), engine=engine)

    with TestClient(app) as client:
        wait_until_ready(client)
        assert engine.ready is True
        assert len(engine.calls) == 1
        assert engine.calls[0].priority == Priority.LIVE

    assert engine.ready is False


def test_health_turns_ready_once_the_engine_has_loaded() -> None:
    engine = SlowEngine()

    with TestClient(create_app(VLLM_SETTINGS, engine=engine)) as client:
        loading = client.get("/api/health")
        assert loading.status_code == 200
        assert loading.json()["ready"] is False

        engine.release.set()
        assert wait_until_ready(client)["engine"] == "vllm"

    assert engine.ready is False


def test_shutdown_while_loading_closes_the_engine() -> None:
    engine = SlowEngine()

    with TestClient(create_app(VLLM_SETTINGS, engine=engine)) as client:
        assert client.get("/api/health").json()["ready"] is False
        # Shutdown cancels the load at once; the timer then lets the worker thread end.
        threading.Timer(0.3, engine.release.set).start()

    assert engine.ready is False
    assert engine.calls == []


def test_engine_not_ready_returns_503() -> None:
    app = create_app(VLLM_SETTINGS, engine=FakeEngine(model=DEFAULT_MODEL))

    @app.get("/test/generate")
    async def generate(request: Request) -> dict[str, str]:
        result = await request.app.state.engine.generate(
            "p", priority=Priority.LIVE, max_tokens=WARMUP_MAX_TOKENS
        )
        return {"text": result.text}

    # Without `with`, the lifespan does not run, so the engine never starts.
    response = TestClient(app).get("/test/generate")

    assert response.status_code == 503
    assert_error_shape(response.json(), "engine_not_ready")


def test_failed_start_is_logged_and_leaves_the_engine_closed(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = BrokenEngine(model=DEFAULT_MODEL)

    asyncio.run(open_engine(engine))

    assert engine.ready is False
    assert "failed to start" in caplog.text
    assert "out of GPU memory" in caplog.text


def test_open_engine_logs_startup_time_and_warm_up(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="telereason")
    engine = FakeEngine(model=DEFAULT_MODEL)

    asyncio.run(open_engine(engine))

    assert engine.ready is True
    assert "fake engine ready in" in caplog.text
    assert "warm-up:" in caplog.text
