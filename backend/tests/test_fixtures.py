import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from telereason.api.schemas import (
    AnswerEvent,
    AskRequest,
    DoneEvent,
    ErrorResponse,
    ExamplesResponse,
    GateResponse,
    HealthResponse,
    NightRunProgress,
    NightRunStarted,
    ReportResponse,
    SystemResponse,
    ToolCallEvent,
    ToolResultEvent,
    WindowDetail,
    WindowListResponse,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"

JSON_FIXTURES: dict[str, type[BaseModel]] = {
    "health.json": HealthResponse,
    "system.json": SystemResponse,
    "gate.json": GateResponse,
    "report_latest.json": ReportResponse,
    "windows.json": WindowListResponse,
    "ask_request.json": AskRequest,
    "examples.json": ExamplesResponse,
    "night_run_started.json": NightRunStarted,
    "night_run.json": NightRunProgress,
}
WINDOW_DETAIL = "window_detail.json"
ASK_STREAM = "ask.sse"

SSE_EVENTS: dict[str, type[BaseModel]] = {
    "tool_call": ToolCallEvent,
    "tool_result": ToolResultEvent,
    "answer": AnswerEvent,
    "done": DoneEvent,
    "error": ErrorResponse,
}

# Fault class names from the 11 Oct results (m_hazirlik.json `kok41`; the FactoryWave
# subset includes kapi_gpu.json `k1.tek_gruplu_sinif`). None may appear in a fixture:
# FactoryWave is `closed` and AURSAD is `screening`, so neither line names a cause.
FACTORYWAVE_FAULTS = (
    "additional_axis_payload",
    "collision_cardboard_object",
    "collision_foam_object",
    "collision_hanging_cable",
    "collision_rigid_object",
    "external_arm_disturbance",
    "fixture_displacement",
    "gripper_activation_failure",
    "gripper_release_during_motion",
    "hole_obstruction",
    "incorrect_insertion_depth",
    "invalid_gripping_position",
    "joint_position_limit_violation",
    "peg_insertion_misalignment",
    "peg_surface_contamination",
    "self_collision_link_interference",
    "unstable_mounting_platform",
)
AURSAD_FAULTS = (
    "damaged_plate_thread",
    "damaged_screw_thread",
    "extra_assembly_component",
    "missing_screw",
)


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def assert_round_trip(model: type[BaseModel], data: Any) -> None:
    # Validating alone ignores unknown keys; the round trip also catches extra or missing fields.
    assert model.model_validate(data).model_dump(mode="json") == data


def parse_sse(text: str) -> list[tuple[str, Any]]:
    messages = []
    for block in text.strip().split("\n\n"):
        lines = [line for line in block.splitlines() if not line.startswith(":")]  # skip pings
        fields = dict(line.split(": ", 1) for line in lines)
        assert set(fields) == {"event", "data"}, block
        messages.append((fields["event"], json.loads(fields["data"])))
    return messages


def fault_names_in(text: str) -> list[str]:
    lowered = text.lower()
    return [
        name
        for name in FACTORYWAVE_FAULTS + AURSAD_FAULTS
        if name in lowered or name.replace("_", " ") in lowered
    ]


@pytest.mark.parametrize("name", sorted(JSON_FIXTURES))
def test_json_fixture_matches_schema(name: str) -> None:
    assert_round_trip(JSON_FIXTURES[name], load(name))


def test_window_detail_fixture_matches_schema() -> None:
    windows = load(WINDOW_DETAIL)
    assert windows
    for window_id, window in windows.items():
        assert_round_trip(WindowDetail, window)
        assert window["window_id"] == window_id


def test_ask_stream_matches_event_models() -> None:
    messages = parse_sse((FIXTURES / ASK_STREAM).read_text(encoding="utf-8"))
    for event, data in messages:
        assert_round_trip(SSE_EVENTS[event], data)
    assert messages[-1][0] == "done"


def test_ask_stream_shows_closed_line_answer() -> None:
    messages = dict(parse_sse((FIXTURES / ASK_STREAM).read_text(encoding="utf-8")))
    window_id = load("ask_request.json")["window_id"]
    assert load(WINDOW_DETAIL)[window_id]["verdict"] == "closed"
    assert messages["tool_result"]["summary"] == "diagnosis closed on this line"
    assert messages["answer"]["answer"] == "DIAGNOSIS_CLOSED"


def test_every_fixture_file_is_covered() -> None:
    covered = set(JSON_FIXTURES) | {WINDOW_DETAIL, ASK_STREAM, "README.md"}
    assert {p.name for p in FIXTURES.iterdir()} == covered


@pytest.mark.parametrize("name", sorted(p.name for p in FIXTURES.iterdir()))
def test_no_fault_name_in_fixtures(name: str) -> None:
    assert fault_names_in((FIXTURES / name).read_text(encoding="utf-8")) == []


def test_fault_name_check_catches_both_spellings() -> None:
    assert fault_names_in("Likely Hole Obstruction.") == ["hole_obstruction"]
    assert fault_names_in('{"answer": "missing_screw"}') == ["missing_screw"]
