"""Closed-line rule end to end (#37): on a `closed` line no night note and no live answer names
a fault, even when the model tries.

A small night runs through the API with a stand-in gate (FactoryWave `closed`) and a fake
engine whose every night note names the fault; then the assistant is asked about a FactoryWave
window and the model names the fault again. Nothing the API returns for that line may carry a
fault name.
"""

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from telereason.ai.engine import FakeEngine, Generation, Prompt
from telereason.ai.tools import FAULT_TERMS
from telereason.settings import DEFAULT_MODEL
from telereason.store.db import NightDb
from tests.test_agent import call, final
from tests.test_lifespan import wait_until_ready
from tests.test_night_runs import wait_for_run
from tests.test_night_steps import db_app, night_dataset

# The FactoryWave fault in the synthetic pool is "Collision Foam-Object".
FAULT_NOTE = "The robot hit a foam object: a collision_foam_object, the root cause of the stop."
FAULT_ANSWER = "This looks like a collision with a foam object."
FAULT_SPELLINGS = (
    "collision_foam_object",
    "collision foam object",
    "collision foam-object",
    *FAULT_TERMS,
)


class FaultNamingEngine(FakeEngine):
    """Every night note names the fault; live turns come from the script."""

    def __init__(self) -> None:
        super().__init__(model=DEFAULT_MODEL)

    async def generate(self, prompt: Prompt, **kwargs: Any) -> Generation:
        if kwargs.get("kind") == "note":
            self.script(f"<|channel|>final<|message|>{FAULT_NOTE}<|return|>")
        return await super().generate(prompt, **kwargs)


def fault_names_in(payload: object) -> list[str]:
    text = json.dumps(payload).casefold()
    return [name for name in FAULT_SPELLINGS if name in text]


def test_no_fault_name_reaches_the_closed_line(tmp_path: Path) -> None:
    app = db_app(night_dataset(tmp_path, extra_fw_normals=6), tmp_path)
    engine = FaultNamingEngine()
    app.state.engine = app.state.night_runner.engine = engine

    with TestClient(app) as client:
        wait_until_ready(client)
        run_id = client.post("/api/night-runs", json={}).json()["run_id"]
        assert wait_for_run(client, run_id)["status"] == "done"

        report = client.get("/api/report/latest").json()
        listed = client.get("/api/windows", params={"line": "FactoryWave"}).json()
        details = [client.get(f"/api/windows/{i['window_id']}").json() for i in listed["items"]]
        window_id = details[0]["window_id"]
        engine.script(call("get_window", {}), final(FAULT_ANSWER))
        answer = client.post(
            "/api/ask", json={"question": "Explain this window.", "window_id": window_id}
        )

    closed = next(line for line in report["lines"] if line["line"] == "FactoryWave")
    assert closed["verdict"] == "closed" and closed["items"]
    assert listed["total"] == len(details) == 10
    # The model did try: every stored attempt on the line names the fault.
    db = NightDb(tmp_path / "night.sqlite")
    attempts = [a for d in details for a in db.notes(run_id, d["window_id"])]
    assert attempts and all("collision" in a.text_raw for a in attempts)
    assert not any(a.passed for a in attempts)
    # Nothing the API serves for the closed line carries it.
    assert fault_names_in(closed) == []
    assert fault_names_in(listed) == []
    assert fault_names_in(details) == []
    assert all(d["note"] == "" and not d["verified"] for d in details)
    assert all((d["score"], d["flagged"], d["top_features"]) == (None, None, []) for d in details)
    # The live answer is replaced by the verified facts.
    assert answer.status_code == 200
    assert '"answer": "FACTS_ONLY"' in answer.text
    assert fault_names_in(answer.text) == []
