"""scripts/smoke_ask.py: the question set, the answer checks and a run in fake mode (#44)."""

import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from scripts import smoke_ask
from scripts.smoke_ask import (
    QUESTIONS,
    check,
    class_names,
    fault_names,
    main,
    names_in,
    normalize,
)
from telereason.ai import tools
from telereason.ai.agent import is_action_request
from telereason.ai.tools import ProtocolIndex
from tests.test_agent import KNOWLEDGE_GRAPH
from tests.test_measure_ask import base_url  # noqa: F401  (fixture)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
SET = json.loads(QUESTIONS.read_text(encoding="utf-8"))["questions"]
NAMES = fault_names(KNOWLEDGE_GRAPH) | class_names(["normal", "missing_screw"])
CLOSED_GATE = {"FactoryWave": "closed", "AURSAD": "screening"}


def record(answer: str, source: str = "tool", text: str = "", steps: int = 0) -> dict[str, Any]:
    events = [{"event": "answer"}, {"event": "done"}]
    return {
        "answer": answer,
        "answer_source": source,
        "answer_text": text,
        "tool_steps": steps,
        "error": None,
        "events": events,
    }


def test_fault_names_match_the_protocol_index() -> None:
    assert smoke_ask.FAULT_TERMS == tools.FAULT_TERMS
    assert (
        fault_names(KNOWLEDGE_GRAPH)
        == ProtocolIndex.from_knowledge_graph(KNOWLEDGE_GRAPH).fault_names
    )
    assert fault_names(None) == ProtocolIndex([]).fault_names
    assert normalize("Hole_Obstruction, (2)") == tools.normalize("Hole_Obstruction, (2)")


def test_names_are_found_in_both_spellings_as_whole_words() -> None:
    assert names_in("Likely a Missing Screw.", NAMES) == ["missing screw"]
    assert names_in('{"cause": "loose_gripper_finger"}', NAMES) == ["loose gripper finger"]
    assert names_in("The arm shows a collision.", NAMES) == ["collision"]
    assert names_in("The robot runs normally; screwdriver torque is low.", NAMES) == []


def test_question_set_covers_the_categories() -> None:
    windows = json.loads((FIXTURES / "window_detail.json").read_text(encoding="utf-8"))
    gate = json.loads((FIXTURES / "gate.json").read_text(encoding="utf-8"))
    verdicts = {row["line"]: row["verdict"] for row in gate["lines"]}

    categories = Counter(q["category"] for q in SET)
    assert len(SET) == 10
    assert len({q["id"] for q in SET}) == 10
    assert categories == Counter(
        window=3, fault_closed=1, cause_screening=1, action=2, unanswerable=2, casual=1
    )
    assert {q["lang"] for q in SET} == {"en", "tr"}
    for category in ("action", "unanswerable"):
        assert {q["lang"] for q in SET if q["category"] == category} == {"en", "tr"}
    for q in SET:
        verdict = windows[q["window_id"]]["verdict"] if "window_id" in q else None
        verdict = verdict or verdicts.get(q.get("line", ""))
        if q["category"] == "fault_closed":
            assert verdict == "closed"
        if q["category"] == "cause_screening":
            assert verdict == "screening"
        if q["category"] == "casual":
            assert q["lang"] == "tr"
        # The agent refuses exactly the action requests before the model runs.
        assert is_action_request(q["question"]) == (q["category"] == "action"), q["id"]


def test_action_request_must_be_refused() -> None:
    action = {"category": "action"}

    refused = check(action, record("ACTION_REFUSED"), "screening", CLOSED_GATE, NAMES)
    answered = check(action, record("ANSWERED", "model", steps=1), "screening", CLOSED_GATE, NAMES)

    assert refused["passed"]
    assert refused["checks"]["from_tools"] is None
    assert not answered["passed"]
    assert answered["checks"]["action_refused"] is False


def test_fault_name_on_a_restricted_line_fails() -> None:
    question = {"category": "fault_closed"}
    named = record("ANSWERED", "model", "It was a missing_screw.", steps=1)

    result = check(question, named, "closed", CLOSED_GATE, NAMES)

    assert not result["passed"]
    assert result["checks"]["no_fault_name"] is False
    assert result["fault_names_found"] == ["missing screw"]
    # Diagnosis is allowed on an open line.
    assert check(question, named, "open", CLOSED_GATE, NAMES)["passed"]


def test_question_without_a_line_is_checked_while_no_line_is_open() -> None:
    named = record("ANSWERED", "model", "A collision.", steps=1)
    open_gate = {"FactoryWave": "closed", "AURSAD": "open"}

    assert check({}, named, None, CLOSED_GATE, NAMES)["checks"]["no_fault_name"] is False
    assert check({}, named, None, open_gate, NAMES)["checks"]["no_fault_name"] is None


def test_model_answer_without_tools_fails() -> None:
    result = check({"category": "unanswerable"}, record("ANSWERED", "model"), None, {}, NAMES)

    assert result["checks"]["from_tools"] is False
    assert check({}, record("NOT_ANSWERABLE"), None, {}, NAMES)["checks"]["from_tools"] is True


def test_broken_stream_fails() -> None:
    broken = {**record("ANSWERED", "model", steps=1), "error": "timeout"}

    assert check({}, broken, None, {}, NAMES)["checks"]["stream"] is False


def test_main_in_fake_mode(base_url: str, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:  # noqa: F811
    graph = tmp_path / "data" / smoke_ask.KNOWLEDGE_GRAPH
    graph.parent.mkdir(parents=True)
    graph.write_text(json.dumps(KNOWLEDGE_GRAPH), encoding="utf-8")
    gate_json = tmp_path / "gate.json"
    gate_json.write_text(json.dumps({"classes": ["normal", "missing_screw"]}), encoding="utf-8")
    out = tmp_path / "smoke.json"
    args = ["--base-url", base_url, "--out", str(out), "--data-dir", str(tmp_path / "data")]

    with caplog.at_level("INFO", logger="smoke_ask"):
        code = main([*args, "--gate-json", str(gate_json)])

    output = json.loads(out.read_text(encoding="utf-8"))
    results = {r["id"]: r for r in output["results"]}
    # Fake mode replays the closed-line answer for every question, so only the two action
    # requests fail.
    assert code == 1
    assert output["summary"] == (
        "smoke_ask: 8/10 passed; stream 10/10, from tools 8/8, action refused 0/2, "
        "no fault name 10/10; failed: q06, q07"
    )
    assert output["summary"] in caplog.text
    assert output["terms"]["class_labels"] == 1
    assert output["terms"]["fault_names"] == len(NAMES)
    assert (results["q02"]["line"], results["q02"]["verdict"]) == ("FactoryWave", "closed")
    assert (results["q05"]["line"], results["q05"]["verdict"]) == ("AURSAD", "screening")
    assert results["q01"]["answer"] == "DIAGNOSIS_CLOSED"
    assert [e["event"] for e in results["q01"]["events"]][-1] == "done"
