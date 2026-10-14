import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from telereason.ai.agent import (
    MAX_TOOL_STEPS,
    NOT_ANSWERABLE_TEXT,
    REFUSAL_TEXT,
    AgentMessage,
    ask,
    is_action_request,
    parse_harmony,
)
from telereason.ai.engine import FakeEngine, Priority
from telereason.ai.tools import TOOLS, AgentData, ProtocolIndex
from telereason.api.schemas import AnswerEvent, GateResponse, ReportResponse
from telereason.data.loader import DataStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
HIDDEN = "hidden analysis 7f3a"

# Synthetic knowledge graph: invented faults, same shape as FactoryBench's mapping.
KNOWLEDGE_GRAPH = {
    "root_cause_error_mapping": [
        {
            "fault_id": 1,
            "root_cause": "loose_gripper_finger",
            "ur3_error_name": "Gripper slip detected",
            "ur3_description": "A finger screw has worked loose.",
            "ur3_protocol": "Lock out the cell, tighten the finger screws, run a slow test cycle.",
        },
        {
            "fault_id": 2,
            "root_cause": "collision_rigid_object",
            "ur3_error_name": "Protective Stop: Collision detected by joint",
            "ur3_description": "The arm hit a rigid object.",
            "ur3_protocol": "Clear the work area and inspect the arm before resuming.",
        },
    ]
}


def fact(signal: str, z: float) -> dict[str, Any]:
    return {
        "signal": signal,
        "window_mean": 1.0,
        "normal_mean": 0.0,
        "difference": 1.0,
        "z": z,
        "plain": f"Clearly off normal ({abs(z)} standard deviations).",
    }


def window(window_id: str, line: str, verdict: str, score: float | None) -> dict[str, Any]:
    return {
        "window_id": window_id,
        "line": line,
        "task": "aursad" if line == "AURSAD" else "pick_and_place",
        "verdict": verdict,
        "score": score,
        "flagged": None if score is None else score >= 0.9,
        "look_first": "vibration_1",
        "facts": [fact("vibration_1", 4.2)],
        "top_features": [],
        "note": "",
        "verified": True,
    }


@pytest.fixture
def data(tmp_path: Path) -> AgentData:
    details = {
        "W-FW1": window("W-FW1", "FactoryWave", "closed", None),
        "W-AU1": window("W-AU1", "AURSAD", "screening", 0.97),
    }
    keys = ("window_id", "line", "task", "score", "flagged", "look_first", "verified")
    items = [{key: row[key] for key in keys} for row in details.values()]
    (tmp_path / "windows.json").write_text(json.dumps({"items": items}), encoding="utf-8")
    (tmp_path / "window_detail.json").write_text(json.dumps(details), encoding="utf-8")
    return AgentData(
        store=DataStore(tmp_path / "data", use_fixtures=True, fixture_root=tmp_path),
        gate=GateResponse.model_validate_json((FIXTURES / "gate.json").read_text("utf-8")),
        protocols=ProtocolIndex.from_knowledge_graph(KNOWLEDGE_GRAPH),
        report=ReportResponse.model_validate_json(
            (FIXTURES / "report_latest.json").read_text("utf-8")
        ),
    )


def call(tool: str, args: dict[str, Any]) -> str:
    return (
        f"<|channel|>analysis<|message|>{HIDDEN}<|end|><|start|>assistant<|channel|>commentary "
        f"to=functions.{tool} <|constrain|>json<|message|>{json.dumps(args)}<|call|>"
    )


def final(text: str) -> str:
    return (
        f"<|channel|>analysis<|message|>{HIDDEN}<|end|>"
        f"<|start|>assistant<|channel|>final<|message|>{text}<|return|>"
    )


def run(
    data: AgentData, question: str, *turns: str, window_id: str | None = None
) -> tuple[list[AgentMessage], FakeEngine]:
    engine = FakeEngine(scripted=turns)

    async def collect() -> list[AgentMessage]:
        await engine.start()
        return [message async for message in ask(engine, data, question, window_id)]

    return asyncio.run(collect()), engine


def answer_of(messages: list[AgentMessage]) -> AnswerEvent:
    data = messages[-2].data
    assert isinstance(data, AnswerEvent)
    return data


def test_five_narrow_tools() -> None:
    assert set(TOOLS) == {
        "get_report",
        "list_windows",
        "get_window",
        "classify_fault",
        "search_protocols",
    }


def test_parser_separates_channels() -> None:
    text = call("get_window", {"window_id": "W-1"})
    analysis, tool_call = parse_harmony(text)
    assert (analysis.channel, analysis.recipient, analysis.content) == ("analysis", None, HIDDEN)
    assert (tool_call.channel, tool_call.recipient) == ("commentary", "functions.get_window")
    assert json.loads(tool_call.content) == {"window_id": "W-1"}

    # The recipient may also stand in the role header, before the channel.
    (moved,) = parse_harmony(
        "<|start|>assistant to=functions.get_report<|channel|>commentary json<|message|>{}"
    )
    assert (moved.channel, moved.recipient, moved.content) == (
        "commentary",
        "functions.get_report",
        "{}",
    )

    (answer,) = parse_harmony("<|channel|>final<|message|> Done. <|return|>")
    assert (answer.channel, answer.content) == ("final", "Done.")


def test_tool_call_then_model_answer(data: AgentData) -> None:
    messages, engine = run(
        data,
        "Which signal should I check first?",
        call("get_window", {}),
        final("Check vibration_1 first."),
        window_id="W-AU1",
    )

    assert [m.event for m in messages] == ["tool_call", "tool_result", "answer", "done"]
    tool_call, tool_result = messages[0].data, messages[1].data
    assert (tool_call.turn, tool_call.tool, tool_call.args) == (1, "get_window", {})
    assert tool_result.summary == "W-AU1: 1 facts, look first at vibration_1"
    assert answer_of(messages) == AnswerEvent(
        answer="ANSWERED", answer_source="model", text="Check vibration_1 first."
    )
    assert all(HIDDEN not in m.model_dump_json() for m in messages)

    assert [c.priority for c in engine.calls] == [Priority.LIVE, Priority.LIVE]
    assert {c.kind for c in engine.calls} == {"agent"}
    second = str(engine.calls[1].prompt)
    assert "to=functions.get_window <|constrain|>json<|message|>{}<|call|>" in second
    tool_output = "<|start|>functions.get_window to=assistant<|channel|>commentary<|message|>"
    assert tool_output + '{"window_id":"W-AU1"' in second
    assert second.endswith("<|start|>assistant")


def test_tool_steps_are_capped_and_the_last_turn_is_forced(data: AgentData) -> None:
    turns = [call("list_windows", {"limit": 2})] * (MAX_TOOL_STEPS + 2)
    messages, engine = run(data, "List the windows.", *turns)

    assert [m.event for m in messages].count("tool_call") == MAX_TOOL_STEPS
    assert len(engine.calls) == MAX_TOOL_STEPS + 1
    assert str(engine.calls[-1].prompt).endswith("<|start|>assistant<|channel|>final<|message|>")
    # The forced turn got a tool call, not an answer: nothing usable, so not answerable.
    assert answer_of(messages) == AnswerEvent(
        answer="NOT_ANSWERABLE", answer_source="tool", text=NOT_ANSWERABLE_TEXT
    )


def test_forced_turn_text_is_the_answer(data: AgentData) -> None:
    turns = [call("get_report", {})] * MAX_TOOL_STEPS + ["Two lines were scanned.<|return|>"]
    messages, _ = run(data, "Summarise the report.", *turns)
    assert answer_of(messages).text == "Two lines were scanned."


@pytest.mark.parametrize(
    "question",
    [
        "Stop the robot now.",
        "Can you restart cell 3?",
        "Please shut down line 2",
        "Robotu durdur.",
        "Hattı yeniden başlatır mısın?",
        "Robotu durdurabilir misin?",
    ],
)
def test_action_requests_are_refused_without_the_model(data: AgentData, question: str) -> None:
    messages, engine = run(data, question)
    assert engine.calls == []
    assert [m.event for m in messages] == ["answer", "done"]
    answer = answer_of(messages)
    assert answer == AnswerEvent(answer="ACTION_REFUSED", answer_source="tool", text=REFUSAL_TEXT)


@pytest.mark.parametrize(
    "question", ["Why did the robot stop?", "Robot neden durdu?", "What does a reset change?"]
)
def test_questions_about_actions_are_not_refused(question: str) -> None:
    assert not is_action_request(question)


def test_out_of_scope_is_not_answerable(data: AgentData) -> None:
    messages, _ = run(data, "Who won the football match?", final("NOT_ANSWERABLE"))
    assert answer_of(messages) == AnswerEvent(
        answer="NOT_ANSWERABLE", answer_source="tool", text=NOT_ANSWERABLE_TEXT
    )


def test_fault_question_on_closed_line_is_answered_by_code(data: AgentData) -> None:
    messages, engine = run(
        data, "What went wrong here?", call("classify_fault", {}), window_id="W-FW1"
    )
    assert len(engine.calls) == 1
    assert messages[1].data.summary == "diagnosis closed on this line"
    answer = answer_of(messages)
    assert (answer.answer, answer.answer_source) == ("DIAGNOSIS_CLOSED", "tool")
    assert answer.text.startswith("Diagnosis is closed on FactoryWave.")
    assert "AUROC 0.529, interval 0.323 to 0.706" in answer.text
    assert "Only verified facts are reported for W-FW1." in answer.text


def test_screening_line_gives_score_not_cause(data: AgentData) -> None:
    messages, _ = run(
        data, "What is wrong with W-AU1?", call("classify_fault", {"window_id": "W-AU1"})
    )
    answer = answer_of(messages)
    assert (answer.answer, answer.answer_source) == ("SCREENING_ONLY", "tool")
    assert (
        "The abnormal-behaviour score for W-AU1 is 0.97. It is flagged for review." in answer.text
    )


@pytest.mark.parametrize("window_id", ["W-FW1", "W-AU1"])
def test_fault_name_is_blocked_on_restricted_lines(data: AgentData, window_id: str) -> None:
    messages, _ = run(
        data,
        "Explain this window.",
        call("get_window", {}),
        final("This looks like a collision with a rigid object."),
        window_id=window_id,
    )
    answer = answer_of(messages)
    assert (answer.answer, answer.answer_source) == ("FACTS_ONLY", "tool")
    assert "collision" not in answer.text.casefold()
    assert "vibration_1: Clearly off normal" in answer.text


def test_fault_name_without_any_tool_is_not_answered(data: AgentData) -> None:
    messages, _ = run(data, "What usually breaks?", final("Mostly a loose gripper finger."))
    assert answer_of(messages) == AnswerEvent(
        answer="NOT_ANSWERABLE", answer_source="tool", text=NOT_ANSWERABLE_TEXT
    )


def test_protocols_are_blocked_on_restricted_lines(data: AgentData) -> None:
    messages, _ = run(
        data,
        "Which protocol applies?",
        call("search_protocols", {"query": "gripper slip"}),
        final("NOT_ANSWERABLE"),
        window_id="W-FW1",
    )
    assert "not available" in messages[1].data.summary


def test_protocol_answer_may_name_its_fault(data: AgentData) -> None:
    messages, engine = run(
        data,
        "What is the protocol for a gripper slip?",
        call("search_protocols", {"query": "gripper slip"}),
        final("For a loose gripper finger: lock out the cell and tighten the finger screws."),
    )
    assert messages[1].data.summary == "1 matching protocols"
    assert "loose gripper finger" not in messages[1].model_dump_json()
    assert '"fault":"loose_gripper_finger"' in str(engine.calls[1].prompt)
    answer = answer_of(messages)
    assert (answer.answer, answer.answer_source) == ("ANSWERED", "model")


def test_bad_tool_calls_return_errors_to_the_model(data: AgentData) -> None:
    messages, engine = run(
        data,
        "Show me W-404.",
        call("reboot_robot", {}),
        call("get_window", {"window_id": "W-404"}),
        call("list_windows", {"limit": 50}),
        final("NOT_ANSWERABLE"),
    )
    summaries = [m.data.summary for m in messages if m.event == "tool_result"]
    assert summaries == [
        "unknown tool reboot_robot",
        "window W-404 does not exist",
        "invalid arguments for list_windows: limit",
    ]
    assert '{"error":"unknown tool reboot_robot"}' in str(engine.calls[1].prompt)


def test_canned_fake_turn_answers(data: AgentData) -> None:
    messages, _ = run(data, "What can you do?")
    answer = answer_of(messages)
    assert answer.answer_source == "model"
    assert answer.text.startswith("I can answer questions about last night's report")
    assert messages[-1].data.tokens > 0


def test_missing_knowledge_graph_gives_an_empty_index(tmp_path: Path) -> None:
    index = ProtocolIndex.load(tmp_path)
    assert index.protocols == []
    assert index.search("gripper") == []


def test_cut_off_analysis_forces_the_final_turn(data: AgentData) -> None:
    turns = [f"<|channel|>analysis<|message|>{HIDDEN}", "The report has two lines."]
    messages, engine = run(data, "Summarise the report.", *turns)
    assert len(engine.calls) == 2
    second = str(engine.calls[1].prompt)
    assert second.endswith("<|start|>assistant<|channel|>final<|message|>")
    assert answer_of(messages).text == "The report has two lines."
    assert all(HIDDEN not in m.model_dump_json() for m in messages)
