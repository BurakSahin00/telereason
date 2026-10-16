import asyncio
import json

import pytest

from telereason.ai.engine import FAKE_NOTE, FakeEngine, Priority
from telereason.ai.notes import (
    INSTRUCTIONS,
    NOTE_MAX_TOKENS,
    NoteResult,
    note_mode,
    note_prompt,
    note_seed,
    write_note,
)
from telereason.ai.verifier import forbidden_terms
from telereason.night.facts import WindowFacts

TERMS = forbidden_terms(["normal", "missing_screw", "damaged_plate_thread"])

FACTS = WindowFacts(
    window_id="W-123456",
    ms_id="item-1",
    line="AURSAD",
    task="aursad",
    reference="other experiment blocks",
    deviating=[
        {
            "signal": "setpoint_acc_5",
            "window_mean": -0.0208,
            "normal_mean": -0.00139,
            "z": -8.36,
            "difference": -0.0194,
        },
        {
            "signal": "vibration_1",
            "window_mean": 2.5,
            "normal_mean": 1.25,
            "z": 3.1,
            "difference": 1.25,
        },
    ],
    gap={
        "pair": "feedback_pos_5 vs setpoint_pos_5",
        "max_abs_gap": 0.07,
        "normal_typical": 0.0237,
        "z": 9.5,
        "excess_over_typical": 0.0463,
    },
    missing_signals=1,
    look_first="feedback_pos_5 vs setpoint_pos_5",
)
SCORE = 0.912


def facts_json(mode: str) -> str:
    return json.dumps(FACTS.note_input(SCORE if mode == "screening" else None))


PASSING = (
    "The abnormal-behaviour score is **0.912**. setpoint_acc_5 deviates most "
    "(z = -8.36, difference -0.0194). A technician should look first at "
    "feedback_pos_5 vs setpoint_pos_5."
)
FAILING = "setpoint_pos 5 deviates (z = -8.36), 120% higher than normal."


def final(text: str) -> str:
    return (
        "<|channel|>analysis<|message|>Copy the numbers.<|end|>"
        f"<|start|>assistant<|channel|>final<|message|>{text}"
    )


def run_note(engine: FakeEngine, mode: str = "screening") -> NoteResult:
    asyncio.run(engine.start())
    return asyncio.run(
        write_note(engine, facts_json(mode), window_id=FACTS.window_id, mode=mode, terms=TERMS)  # type: ignore[arg-type]
    )


# Prompt


@pytest.mark.parametrize(
    ("verdict", "mode"), [("closed", "closed"), ("screening", "screening"), ("open", "screening")]
)
def test_note_mode_follows_the_gate_verdict(verdict: str, mode: str) -> None:
    assert note_mode(verdict) == mode  # type: ignore[arg-type]


def test_prompt_holds_the_mode_instructions_and_the_facts() -> None:
    closed_json = facts_json("closed")

    prompt = note_prompt(closed_json, "closed")

    assert INSTRUCTIONS["closed"] in prompt
    assert INSTRUCTIONS["screening"] not in prompt
    assert "CLOSED" in INSTRUCTIONS["closed"]
    assert f"<|start|>user<|message|>{closed_json}<|end|>" in prompt
    assert prompt.endswith("<|start|>assistant")


def test_seed_is_tied_to_the_window_and_changes_on_retry() -> None:
    assert note_seed("W-123456", 0) == note_seed("W-123456", 0)
    assert note_seed("W-123456", 0) != note_seed("W-123456", 1)
    assert note_seed("W-123456", 0) != note_seed("W-654321", 0)
    assert 0 <= note_seed("W-123456", 1) < 2**31


# Writing and verifying


def test_a_passing_note_is_written_once_and_cleaned() -> None:
    engine = FakeEngine(scripted=[final(PASSING)])

    result = run_note(engine)

    assert len(result.attempts) == 1
    assert result.first_passed and result.verified
    assert "**" not in result.note
    assert result.note.startswith("The abnormal-behaviour score is 0.912.")
    (call,) = engine.calls
    assert call.priority == Priority.NIGHT
    assert call.kind == "note"
    assert call.seed == note_seed("W-123456", 0)


def test_a_failing_note_is_retried_with_another_seed_and_can_pass() -> None:
    engine = FakeEngine(scripted=[final(FAILING), final(PASSING)])

    result = run_note(engine)

    assert [a.passed for a in result.attempts] == [False, True]
    assert result.attempts[0].check.unsourced == ("5", "120%")
    assert not result.first_passed
    assert result.verified
    assert result.note == result.attempts[1].text
    assert [c.seed for c in engine.calls] == [note_seed("W-123456", 0), note_seed("W-123456", 1)]


def test_a_note_that_fails_twice_is_dropped() -> None:
    engine = FakeEngine(scripted=[final(FAILING), final("The root cause is a missing screw.")])

    result = run_note(engine)

    assert len(engine.calls) == 2
    assert not result.verified
    assert result.note == ""
    assert "root cause" in result.attempts[1].check.violations


def test_a_note_without_a_final_channel_fails() -> None:
    engine = FakeEngine(scripted=["<|channel|>analysis<|message|>Thinking...", final(PASSING)])

    result = run_note(engine)

    assert not result.attempts[0].check.present
    assert result.attempts[0].text == ""
    assert result.verified


def test_a_note_cut_off_at_the_token_limit_fails() -> None:
    engine = FakeEngine(scripted=[final("x" * NOTE_MAX_TOKENS), final("x" * NOTE_MAX_TOKENS)])

    result = run_note(engine)

    assert all(a.cut_off for a in result.attempts)
    assert not result.verified


def test_a_fault_name_on_a_closed_line_never_reaches_the_note() -> None:
    engine = FakeEngine(
        scripted=[final("This looks like damaged_plate_thread."), final("A missing screw.")]
    )

    result = run_note(engine, mode="closed")

    assert not result.verified
    assert result.note == ""


def test_the_canned_fake_note_passes() -> None:
    result = run_note(FakeEngine())

    assert result.verified
    assert result.note == FAKE_NOTE
