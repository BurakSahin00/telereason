"""A small night on request (#41): window choice, progress, live answers during the scan, and
the pool built at startup."""

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from telereason.ai.engine import FakeEngine, Generation, Prompt
from telereason.api.main import create_app
from telereason.night.steps import pick_windows
from telereason.night.windows import Window
from telereason.settings import DEFAULT_MODEL, Settings
from telereason.store.db import NightDb
from tests.test_lifespan import wait_until_ready
from tests.test_night_facts import _window
from tests.test_night_runs import wait_for_run
from tests.test_night_steps import db_app, night_dataset
from tests.test_report import fixture_night


def windows(aursad: int, factorywave: int) -> list[Window]:
    made = [
        _window(f"W-{n:06d}", "AURSAD", "au:b00", "normal", {"x": [1.0]}) for n in range(aursad)
    ]
    made += [
        _window(f"W-{n:06d}", "FactoryWave", "gun:x", "normal", {"x": [1.0]}, task="screwing")
        for n in range(aursad, aursad + factorywave)
    ]
    return made


def lines(picked: list[Window]) -> dict[str, int]:
    return {line: sum(w.line == line for w in picked) for line in ("AURSAD", "FactoryWave")}


def test_a_small_night_keeps_the_line_proportion() -> None:
    pool = windows(4068, 3354)

    picked = pick_windows(pool, 300, "run-20261016-1")

    assert len(picked) == 300
    assert lines(picked) == {"AURSAD": 165, "FactoryWave": 135}
    # Night order is kept.
    assert [w.window_id for w in picked] == sorted(w.window_id for w in picked)


def test_the_choice_depends_on_the_run_id_only() -> None:
    pool = windows(40, 30)

    first = [w.window_id for w in pick_windows(pool, 10, "run-20261016-1")]
    again = [w.window_id for w in pick_windows(list(pool), 10, "run-20261016-1")]
    other = [w.window_id for w in pick_windows(pool, 10, "run-20261016-2")]

    assert first == again
    assert first != other


def test_a_short_line_leaves_its_share_to_the_other() -> None:
    pool = windows(2, 100)

    picked = pick_windows(pool, 50, "run-1")

    assert len(picked) == 50
    assert lines(picked)["AURSAD"] <= 2


def test_a_night_larger_than_the_pool_takes_every_window() -> None:
    pool = windows(3, 2)

    assert pick_windows(pool, 300, "run-1") == pool


def test_a_small_night_scans_only_its_windows(tmp_path: Path) -> None:
    app = db_app(night_dataset(tmp_path), tmp_path)

    with TestClient(app) as client:
        wait_until_ready(client)
        run_id = client.post("/api/night-runs", json={"max_windows": 5}).json()["run_id"]
        finished = wait_for_run(client, run_id)
        report = client.get("/api/report/latest").json()

    assert (finished["status"], finished["step"], finished["done"], finished["total"]) == (
        "done",
        "report",
        5,
        5,
    )
    assert {line["line"]: line["scanned"] for line in report["lines"]} == {
        "AURSAD": 4,
        "FactoryWave": 1,
    }


class HeldNotes(FakeEngine):
    """Night notes wait until the test releases them; live questions do not."""

    def __init__(self) -> None:
        super().__init__(model=DEFAULT_MODEL)
        self.release = threading.Event()

    async def generate(self, prompt: Prompt, **kwargs: Any) -> Generation:
        if kwargs.get("kind") == "note":
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        return await super().generate(prompt, **kwargs)


def test_the_assistant_answers_while_a_small_night_scans(tmp_path: Path) -> None:
    data_dir = night_dataset(tmp_path)
    # A finished night for the assistant to read; its windows are not in this pool, so the
    # small night runs the gate instead of reusing that night's.
    fixture_night(NightDb(tmp_path / "night.sqlite"))
    app = db_app(data_dir, tmp_path, gate_mode="full")
    engine = HeldNotes()
    app.state.engine = app.state.night_runner.engine = engine

    with TestClient(app) as client:
        wait_until_ready(client)
        run_id = client.post("/api/night-runs", json={"max_windows": 5}).json()["run_id"]
        deadline = time.monotonic() + 5
        while client.get(f"/api/night-runs/{run_id}").json()["step"] != "scan":
            assert time.monotonic() < deadline, "the night never reached the scan"
            time.sleep(0.02)
        answer = client.post("/api/ask", json={"question": "What can you do?"})
        during = client.get(f"/api/night-runs/{run_id}").json()
        engine.release.set()
        finished = wait_for_run(client, run_id)

    assert answer.status_code == 200
    assert "event: answer" in answer.text and "event: done" in answer.text
    assert (during["status"], during["step"], during["done"], during["total"]) == (
        "running",
        "scan",
        0,
        5,
    )
    assert finished["status"] == "done"


def test_real_mode_builds_the_pool_at_startup(tmp_path: Path) -> None:
    app = db_app(night_dataset(tmp_path), tmp_path)
    signals = app.state.data_store.night_signals

    with TestClient(app):
        app.state.pool_loading.join(10)
        loaded = signals._night

    assert loaded is not None and len(loaded.pool.windows) == 11


def test_fake_mode_builds_no_pool(tmp_path: Path) -> None:
    settings = Settings(engine="fake", model=DEFAULT_MODEL, data_dir=str(night_dataset(tmp_path)))
    app = create_app(settings)

    with TestClient(app) as client:
        wait_until_ready(client)

    assert not hasattr(app.state, "pool_loading")
    assert app.state.data_store.night_signals._night is None


def test_a_missing_dataset_does_not_stop_startup(tmp_path: Path) -> None:
    settings = Settings(
        engine="vllm",
        model=DEFAULT_MODEL,
        data_dir=str(tmp_path / "nowhere"),
        db_path=str(tmp_path / "night.sqlite"),
    )
    app = create_app(settings, engine=FakeEngine(model=DEFAULT_MODEL))

    with TestClient(app) as client:
        app.state.pool_loading.join(10)
        health = client.get("/api/health")

    assert health.status_code == 200
    assert app.state.data_store.night_signals._night is None
