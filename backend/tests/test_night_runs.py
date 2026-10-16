"""POST/GET /api/night-runs in real mode with stand-in night steps (#49)."""

import asyncio
import re
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telereason.ai.engine import FakeEngine
from telereason.api.main import create_app
from telereason.api.schemas import GateResponse, StepTimings
from telereason.night.run import (
    INTERRUPTED_MESSAGE,
    PROGRESS_INTERVAL_S,
    NightRunner,
    PendingSteps,
    RunContext,
)
from telereason.settings import DEFAULT_MODEL, Settings
from telereason.store.db import NightDb, RunRecord
from tests.test_contract import assert_error_shape
from tests.test_lifespan import SlowEngine, wait_until_ready

RUN_ID = re.compile(r"^run-\d{8}-(\d+)$")
FINISHED = ("done", "failed")


class HeldSteps:
    """Steps that hold the gate until the test releases it, then process three windows."""

    def __init__(self, fail_at: str | None = None) -> None:
        self.release = threading.Event()
        self.in_gate = threading.Event()
        self.fail_at = fail_at
        self.calls: list[str] = []

    async def _step(self, name: str) -> None:
        self.calls.append(name)
        if name == self.fail_at:
            raise RuntimeError(f"{name} broke")

    async def gate(self, ctx: RunContext) -> None:
        self.in_gate.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        await self._step("gate")

    async def facts(self, ctx: RunContext) -> None:
        ctx.progress(0, 3, flush=True)
        await self._step("facts")

    async def scan(self, ctx: RunContext) -> None:
        for done in range(1, 4):
            ctx.progress(done)
        await self._step("scan")

    async def report(self, ctx: RunContext) -> None:
        await self._step("report")


def wait_for_run(client: TestClient, run_id: str, timeout_s: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        body = client.get(f"/api/night-runs/{run_id}").json()
        if body.get("status") in FINISHED:
            return body
        time.sleep(0.02)
    raise AssertionError(f"night run {run_id} did not finish within {timeout_s} s")


def settings(tmp_path: Path) -> Settings:
    # The rate limit has its own tests (test_rate_limit.py); here it would stop the third POST.
    return Settings(
        engine="vllm",
        model=DEFAULT_MODEL,
        db_path=str(tmp_path / "night.sqlite"),
        rate_limit_night_per_min=0,
    )


@pytest.fixture
def steps() -> HeldSteps:
    return HeldSteps()


@pytest.fixture
def app(tmp_path: Path, steps: HeldSteps) -> FastAPI:
    app = create_app(settings(tmp_path), engine=FakeEngine(model=DEFAULT_MODEL))
    app.state.night_runner.steps = steps
    return app


@pytest.fixture
def client(app: FastAPI, steps: HeldSteps) -> Iterator[TestClient]:
    with TestClient(app) as client:
        wait_until_ready(client)
        yield client
        steps.release.set()


def test_a_run_starts_queued_and_runs_its_steps_in_order(
    client: TestClient, steps: HeldSteps
) -> None:
    started = client.post("/api/night-runs", json={"max_windows": 3})
    run_id = started.json()["run_id"]
    assert steps.in_gate.wait(5)
    running = client.get(f"/api/night-runs/{run_id}").json()
    steps.release.set()
    finished = wait_for_run(client, run_id)

    assert started.status_code == 202
    assert started.json()["status"] == "queued"
    assert (match := RUN_ID.match(run_id)) and match.group(1) == "1"
    assert (running["status"], running["step"], running["total"]) == ("running", "gate", 3)
    assert running["timings_s"] == {"gate": None, "facts": None, "scan": None, "report": None}
    assert steps.calls == ["gate", "facts", "scan", "report"]
    assert (finished["status"], finished["step"], finished["done"], finished["total"]) == (
        "done",
        "report",
        3,
        3,
    )
    assert all(isinstance(finished["timings_s"][step], float) for step in steps.calls)


def test_a_second_run_is_busy_while_one_is_active(client: TestClient, steps: HeldSteps) -> None:
    first = client.post("/api/night-runs", json={}).json()
    assert steps.in_gate.wait(5)

    second = client.post("/api/night-runs", json={})
    steps.release.set()
    wait_for_run(client, first["run_id"])
    third = client.post("/api/night-runs", json={})

    assert second.status_code == 409
    assert_error_shape(second.json(), "busy")
    assert third.status_code == 202
    assert (match := RUN_ID.match(third.json()["run_id"])) and match.group(1) == "2"


def test_default_window_count_is_300(client: TestClient, steps: HeldSteps) -> None:
    run_id = client.post("/api/night-runs", json={}).json()["run_id"]
    assert steps.in_gate.wait(5)

    assert client.get(f"/api/night-runs/{run_id}").json()["total"] == 300


def test_a_failing_step_fails_the_run(tmp_path: Path) -> None:
    steps = HeldSteps(fail_at="scan")
    steps.release.set()
    app = create_app(settings(tmp_path), engine=FakeEngine(model=DEFAULT_MODEL))
    app.state.night_runner.steps = steps

    with TestClient(app) as client:
        wait_until_ready(client)
        run_id = client.post("/api/night-runs", json={}).json()["run_id"]
        finished = wait_for_run(client, run_id)

    assert (finished["status"], finished["step"]) == ("failed", "scan")
    timings = finished["timings_s"]
    assert isinstance(timings["gate"], float) and isinstance(timings["facts"], float)
    assert (timings["scan"], timings["report"]) == (None, None)
    run = NightDb(tmp_path / "night.sqlite").get_run(run_id)
    assert run is not None and run.error == "RuntimeError: scan broke"


def test_unknown_run_is_not_found(client: TestClient) -> None:
    response = client.get("/api/night-runs/run-20261016-9")

    assert response.status_code == 404
    assert_error_shape(response.json(), "not_found")


def test_no_run_starts_before_the_engine_is_ready(tmp_path: Path) -> None:
    engine = SlowEngine()
    app = create_app(settings(tmp_path), engine=engine)

    with TestClient(app) as client:
        response = client.post("/api/night-runs", json={})
        engine.release.set()

    assert response.status_code == 503
    assert_error_shape(response.json(), "engine_not_ready")
    assert not (tmp_path / "night.sqlite").exists()


def test_startup_fails_a_run_left_unfinished(tmp_path: Path) -> None:
    db = NightDb(tmp_path / "night.sqlite").init()
    db.create_run(RunRecord(run_id="run-20261016-1", status="running", source="live"))
    app = create_app(settings(tmp_path), engine=FakeEngine(model=DEFAULT_MODEL))

    with TestClient(app) as client:
        body = client.get("/api/night-runs/run-20261016-1").json()

    assert body["status"] == "failed"
    run = db.get_run("run-20261016-1")
    assert run is not None and run.error == INTERRUPTED_MESSAGE


def test_shutdown_fails_the_active_run(tmp_path: Path, steps: HeldSteps) -> None:
    app = create_app(settings(tmp_path), engine=FakeEngine(model=DEFAULT_MODEL))
    app.state.night_runner.steps = steps

    with TestClient(app) as client:
        wait_until_ready(client)
        run_id = client.post("/api/night-runs", json={}).json()["run_id"]
        assert steps.in_gate.wait(5)

    run = NightDb(tmp_path / "night.sqlite").get_run(run_id)
    assert run is not None
    assert (run.status, run.step, run.error) == ("failed", "gate", INTERRUPTED_MESSAGE)


def test_pending_steps_fail_at_the_gate(tmp_path: Path) -> None:
    async def run() -> RunRecord | None:
        runner = NightRunner(NightDb(tmp_path / "night.sqlite"), FakeEngine(), PendingSteps())
        started = runner.start(300)
        await runner.wait()
        return runner.get(started.run_id)

    finished = asyncio.run(run())

    assert finished is not None
    assert (finished.status, finished.step) == ("failed", "gate")
    assert finished.error is not None and "#40" in finished.error


class CountingDb(NightDb):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.writes: list[dict[str, Any]] = []

    def update_run(
        self,
        run_id: str,
        *,
        gate: GateResponse | None = None,
        timings: StepTimings | None = None,
        **columns: Any,
    ) -> None:
        self.writes.append(columns)


def test_progress_is_written_at_most_once_per_interval(tmp_path: Path) -> None:
    now = [0.0]
    db = CountingDb(tmp_path / "night.sqlite")
    ctx = RunContext("run-1", 10, db, FakeEngine(), clock=lambda: now[0])

    ctx.progress(1, 10)
    ctx.progress(2)
    now[0] = PROGRESS_INTERVAL_S / 2
    ctx.progress(3)
    now[0] = PROGRESS_INTERVAL_S
    ctx.progress(4)
    ctx.progress(5, flush=True)

    assert db.writes == [
        {"done": 1, "total": 10},
        {"done": 4, "total": 10},
        {"done": 5, "total": 10},
    ]
