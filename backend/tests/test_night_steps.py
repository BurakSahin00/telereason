"""The real night steps (#40) on a tiny synthetic pool, with a stand-in gate and FakeEngine."""

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from telereason.ai.engine import FakeEngine, Generation, Priority, Prompt
from telereason.ai.notes import INSTRUCTIONS
from telereason.ai.tools import ProtocolIndex
from telereason.api.main import create_app
from telereason.api.schemas import FeatureContribution, GateLine, GateResponse, Line, Verdict
from telereason.night.band import NightSignals
from telereason.night.run import NightRunner, RunContext
from telereason.night.steps import (
    GATE_NOT_WIRED,
    GateOutput,
    GateStep,
    GateWindow,
    NightPipeline,
    night_terms,
)
from telereason.settings import DEFAULT_MODEL, Settings
from telereason.store.db import NightDb, RunRecord
from telereason.store.night import DbNight
from tests.test_lifespan import wait_until_ready
from tests.test_night_band import _rows
from tests.test_night_runs import wait_for_run
from tests.test_night_windows import _dataset, _item, _write_jsonl

VERDICTS: dict[Line, Verdict] = {"FactoryWave": "closed", "AURSAD": "screening"}
CONTRIBUTION = (FeatureContribution(feature="sig_a.mean", contribution=0.4),)


def night_dataset(tmp_path: Path) -> Path:
    """Six normal AURSAD windows and one faulty one; four FactoryWave windows on two days."""
    data_dir = _dataset(tmp_path)
    pool = [
        _item(f"au-n{n}", "aursad", f"experiment_{n + 1}_1", rows=_rows([n, n + 1.0, n + 2.0]))
        for n in range(6)
    ]
    pool.append(
        _item(
            "au-target",
            "aursad",
            "experiment_4094_1",
            root_cause="missing_screw",
            rows=_rows([20.0, 21.0, 22.0, 23.0], gap=0.5),
        )
    )
    pool += [
        _item(f"fw-n{n}", "factorywave", "ep-a" if n % 2 else "ep-b", rows=_rows([n, n + 1.0]))
        for n in range(3)
    ]
    pool.append(
        _item(
            "fw-fault", "factorywave", "ep-a", root_cause="Collision Foam-Object", rows=_rows([9.0])
        )
    )
    _write_jsonl(data_dir / "factorybench" / "factorybench_qa" / "level_4.jsonl", pool)
    return data_dir


def stand_in_gate(signals: NightSignals) -> GateStep:
    """Verdicts as on 11 Oct; every pool window tested; a score on both lines (the closed
    line's must never reach the store)."""

    async def gate(ctx: RunContext) -> GateOutput:
        pool = signals.load().pool
        windows = {
            w.window_id: GateWindow(
                score=0.95 if w.label != "normal" else 0.2, fold=1, top_features=CONTRIBUTION
            )
            for w in pool.windows
        }
        lines = [
            GateLine(
                line=line,
                verdict=verdict,
                label=verdict,
                auroc=0.5,
                auroc_ci=(0.4, 0.6),
                explanation="explanation",
            )
            for line, verdict in VERDICTS.items()
        ]
        return GateOutput(verdicts=GateResponse(run_id="gate", lines=lines), windows=windows)

    return gate


def pipeline(data_dir: Path, *, gate: bool = True, concurrency: int = 64) -> NightPipeline:
    signals = NightSignals(data_dir)
    return NightPipeline(
        signals,
        protocols=lambda: ProtocolIndex([]),
        gate_step=stand_in_gate(signals) if gate else None,
        concurrency=concurrency,
    )


def run_night(
    db: NightDb, engine: FakeEngine, steps: NightPipeline, max_windows: int = 300
) -> RunRecord:
    async def run() -> RunRecord:
        await engine.start()
        runner = NightRunner(db, engine, steps)
        started = runner.start(max_windows)
        await runner.wait()
        run = runner.get(started.run_id)
        assert run is not None
        return run

    return asyncio.run(run())


def final(text: str) -> str:
    return f"<|channel|>final<|message|>{text}<|return|>"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return night_dataset(tmp_path)


@pytest.fixture
def db(tmp_path: Path) -> NightDb:
    return NightDb(tmp_path / "night.sqlite")


def test_a_night_runs_every_step_and_its_report_is_served(data_dir: Path, db: NightDb) -> None:
    engine = FakeEngine()
    steps = pipeline(data_dir)

    run = run_night(db, engine, steps)
    pool = steps.signals.load()
    report = DbNight(db).report()

    assert (run.status, run.step, run.done, run.total) == ("done", "report", 11, 11)
    assert run.timings is not None
    assert run.gate is not None and run.gate.run_id == run.run_id
    assert report.run_id == run.run_id
    assert [(line.line, line.scanned) for line in report.lines] == [
        ("AURSAD", 7),
        ("FactoryWave", 4),
    ]
    assert db.count_windows(run.run_id) == db.count_notes(run.run_id) == 11
    assert len(engine.calls) == 11
    assert {(call.priority, call.kind) for call in engine.calls} == {(Priority.NIGHT, "note")}
    for stored in db.windows(run.run_id):
        facts = pool.facts[stored.window.window_id]
        assert stored.window.facts == tuple(facts.fact_rows())
        assert stored.window.look_first == (facts.look_first or "")
        assert stored.window.ms_id and stored.window.group_id
        assert stored.verified


def test_scores_reach_only_the_screening_line(data_dir: Path, db: NightDb) -> None:
    run = run_night(db, FakeEngine(), pipeline(data_dir))
    windows = {w.window.ms_id: w.window for w in db.windows(run.run_id)}

    target = windows["au-target"]
    assert (target.score, target.flagged, target.fold) == (0.95, True, 1)
    assert target.top_features == CONTRIBUTION
    for ms_id in ("fw-fault", "fw-n0"):
        assert (windows[ms_id].score, windows[ms_id].flagged) == (None, None)
        assert windows[ms_id].top_features == ()


def test_each_line_gets_its_note_mode_and_input(data_dir: Path, db: NightDb) -> None:
    engine = FakeEngine()

    run_night(db, engine, pipeline(data_dir))
    inputs = {}
    for call in engine.calls:
        assert isinstance(call.prompt, str)
        user = call.prompt.split("<|start|>user<|message|>")[1].split("<|end|>")[0]
        facts = json.loads(user)
        inputs[facts["line"]] = (facts, call.prompt)

    aursad, aursad_prompt = inputs["AURSAD"]
    closed, closed_prompt = inputs["FactoryWave"]
    assert "abnormal_score" in aursad and INSTRUCTIONS["screening"] in aursad_prompt
    assert "abnormal_score" not in closed and INSTRUCTIONS["closed"] in closed_prompt
    assert "Collision" not in json.dumps(closed) and "missing_screw" not in json.dumps(aursad)


def test_a_note_that_fails_twice_leaves_facts_only(data_dir: Path, db: NightDb) -> None:
    engine = FakeEngine()
    engine.script(*[final("This is a missing screw, the root cause.")] * 22)

    run = run_night(db, engine, pipeline(data_dir))
    report = DbNight(db).report()

    assert run.status == "done"
    assert len(engine.calls) == 22
    for stored in db.windows(run.run_id):
        assert not stored.verified
        attempts = db.notes(run.run_id, stored.window.window_id)
        assert [a.attempt for a in attempts] == [1, 2]
        assert "root cause" in attempts[0].violations
    assert all(
        item.note == "" and not item.verified for line in report.lines for item in line.items
    )


class CountingEngine(FakeEngine):
    """Counts how many notes are written at the same time."""

    def __init__(self) -> None:
        super().__init__()
        self.running = 0
        self.most = 0

    async def generate(self, prompt: Prompt, **kwargs: Any) -> Generation:
        self.running += 1
        self.most = max(self.most, self.running)
        await asyncio.sleep(0.01)
        try:
            return await super().generate(prompt, **kwargs)
        finally:
            self.running -= 1


def test_the_scan_writes_at_most_the_concurrency_limit_at_once(data_dir: Path, db: NightDb) -> None:
    engine = CountingEngine()

    run = run_night(db, engine, pipeline(data_dir, concurrency=3))

    assert run.status == "done"
    assert engine.most == 3


def test_without_the_gate_module_a_run_stops_at_the_gate(data_dir: Path, db: NightDb) -> None:
    engine = FakeEngine()

    run = run_night(db, engine, pipeline(data_dir, gate=False))

    assert (run.status, run.step) == ("failed", "gate")
    assert run.error == f"NotImplementedError: {GATE_NOT_WIRED}"
    assert engine.calls == []


def test_night_terms_keep_fault_names_whole() -> None:
    terms = night_terms(
        ["normal", "missing_screw", "damaged_plate_thread"],
        ["joint overload", "Collision Foam_Object"],
    )

    assert {"missing_screw", "missing screw", "damaged plate thread"} <= set(terms.names)
    assert {"joint overload", "joint_overload", "collision foam object"} <= set(terms.names)
    assert "collision_foam_object" in terms.names
    assert "normal" not in terms.names
    # Class labels give distinctive words; fault names are matched only as whole names.
    assert {"screw", "damaged", "thread"} <= terms.words
    assert "overload" not in terms.words and "collision" not in terms.words


def db_app(data_dir: Path, tmp_path: Path) -> FastAPI:
    settings = Settings(
        engine="vllm",
        model=DEFAULT_MODEL,
        data_dir=str(data_dir),
        night_source="db",
        db_path=str(tmp_path / "night.sqlite"),
        rate_limit_night_per_min=0,
    )
    app = create_app(settings, engine=FakeEngine(model=DEFAULT_MODEL))
    steps: NightPipeline = app.state.night_runner.steps
    steps.gate_step = stand_in_gate(steps.signals)
    return app


def test_a_night_started_from_the_api_becomes_the_served_night(
    data_dir: Path, tmp_path: Path
) -> None:
    app = db_app(data_dir, tmp_path)

    with TestClient(app) as client:
        wait_until_ready(client)
        started = client.post("/api/night-runs", json={"max_windows": 300})
        run_id = started.json()["run_id"]
        finished = wait_for_run(client, run_id)
        report = client.get("/api/report/latest").json()
        system = client.get("/api/system").json()

    assert started.status_code == 202
    assert (finished["status"], finished["done"], finished["total"]) == ("done", 11, 11)
    assert all(isinstance(value, float) for value in finished["timings_s"].values())
    assert report["run_id"] == system["last_night"]["run_id"] == run_id
    assert app.state.night_runner.steps.signals is app.state.data_store.night_signals
