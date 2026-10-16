import json

import pytest

from telereason.ai.verifier import (
    Check,
    flatten_unicode,
    forbidden_terms,
    json_numbers,
    number_is_sourced,
    verify,
)

# Synthetic facts in the 11 Oct input format.
FACTS = json.dumps(
    {
        "window": "W-123456",
        "abnormal_score": 0.912,
        "abnormal_score_note": "screening model, 0 = normal-like, 1 = abnormal-like",
        "line": "AURSAD",
        "task": "aursad",
        "reference": "normal windows, other experiment blocks",
        "deviating_signals": [
            {
                "signal": "setpoint_acc_5",
                "window_mean": -0.0208,
                "normal_mean": -0.00139,
                "z": -8.36,
                "difference": -0.0194,
            },
            {
                "signal": "gripper_force_0",
                "window_mean": 2.5,
                "normal_mean": 1.25,
                "z": 3.1,
                "difference": 1.25,
            },
        ],
        "largest_plan_vs_measured_gap": {
            "pair": "feedback_pos_5 vs setpoint_pos_5",
            "max_abs_gap": 0.07,
            "normal_typical": 0.0237,
            "z": 4.21,
            "excess_over_typical": 0.0463,
        },
        "missing_signals": 0,
        "look_first": "feedback_pos_5 vs setpoint_pos_5",
    }
)
CLASSES = [
    "normal",
    "missing_screw",
    "damaged_plate_thread",
    "gripper_activation_failure",
    "collision_rigid_object",
]
TERMS = forbidden_terms(CLASSES)

GOOD_NOTE = (
    "The abnormal-behaviour score is 0.912. setpoint_acc_5 deviates most (z = -8.36, "
    "difference -0.0194), followed by gripper_force_0 (z = 3.1, difference 1.25). "
    "A technician should look first at feedback_pos_5 vs setpoint_pos_5."
)


def test_a_note_with_copied_numbers_passes() -> None:
    check = verify(GOOD_NOTE, FACTS, TERMS)

    assert check == Check(present=True)
    assert check.passed and check.numbers_ok


def test_window_id_with_a_non_breaking_hyphen_is_still_an_identifier() -> None:
    # 11 Oct: the model wrote W‑143152; without flattening, 143152 reads as a number.
    note = "Window W‑123456: the score is 0.912 and z = ‑8.36."

    assert verify(note, FACTS, TERMS).passed


def test_a_channel_name_split_by_a_space_leaves_an_unsourced_number() -> None:
    # 11 Oct W-143152, first attempt: "setpoint_pos 5" left the 5 without a source.
    note = "setpoint_pos 5 deviates (z = -8.36)."

    check = verify(note, FACTS, TERMS)

    assert not check.passed
    assert check.unsourced == ("5",)


def test_feedback_position_written_with_a_hyphen_and_a_space_fails() -> None:
    check = verify("Look first at feedback-position 5.", FACTS, TERMS)

    assert check.unsourced == ("5",)


def test_a_computed_difference_fails() -> None:
    check = verify("Together the two signals differ by 1.2306.", FACTS, TERMS)

    assert check.unsourced == ("1.2306",)


def test_a_computed_percentage_fails() -> None:
    check = verify("gripper_force_0 is 120% higher than normal.", FACTS, TERMS)

    assert check.unsourced == ("120%",)


def test_a_percentage_of_a_json_number_passes() -> None:
    assert verify("The score is 91.2%.", FACTS, TERMS).passed


@pytest.mark.parametrize("note", ["Two of 3 signals deviate.", "0 signals are missing; 1 pair."])
def test_small_whole_numbers_are_free(note: str) -> None:
    assert verify(note, FACTS, TERMS).passed


def test_whole_numbers_above_three_need_a_source() -> None:
    assert verify("7 signals deviate.", FACTS, TERMS).unsourced == ("7",)


@pytest.mark.parametrize(
    ("token", "sourced"),
    [
        ("0.91", True),  # rounded to the note's decimals
        ("-8.4", True),
        ("0.0463", True),
        ("0.046", True),
        ("1.26", True),  # within 1 % of 1.25
        ("1.4", False),
        ("123456", False),
        ("4.6e-2", True),
        ("0,912", True),  # decimal comma
    ],
)
def test_number_matching(token: str, sourced: bool) -> None:
    assert number_is_sourced(token, json_numbers(FACTS)) is sourced


@pytest.mark.parametrize(
    ("note", "violation"),
    [
        ("The root cause is unclear.", "root cause"),
        ("This was caused by wear.", "caused by"),
        ("Probable cause: unknown.", "probable cause"),
        ("This looks like missing_screw.", "missing_screw"),
        ("This looks like a missing screw.", "missing screw"),
        ("The plate threads may be damaged.", "damaged"),
        ("Check the screws.", "screw"),
        ("A collision is likely.", "collision"),
    ],
)
def test_fault_names_and_cause_phrases_fail(note: str, violation: str) -> None:
    check = verify(note, FACTS, TERMS)

    assert not check.passed
    assert violation in check.violations


def test_class_words_that_appear_in_the_facts_are_allowed() -> None:
    # "gripper" is part of a fault name but also of the signal gripper_force_0.
    assert verify("The gripper force is high.", FACTS, TERMS).passed


def test_stop_words_and_normal_are_not_fault_words() -> None:
    assert "normal" not in TERMS.names
    assert not {"missing", "plate", "object"} & TERMS.words
    assert verify("The object is missing from the normal plate.", FACTS, TERMS).passed


@pytest.mark.parametrize("note", [None, "", "   \n"])
def test_an_empty_note_fails(note: str | None) -> None:
    check = verify(note, FACTS, TERMS)

    assert check == Check(present=False)
    assert not check.passed and not check.numbers_ok


def test_flatten_unicode_maps_dashes_and_spaces_and_drops_invisible_characters() -> None:
    text = "a‑b−c–d e f　g​h﻿i"

    assert flatten_unicode(text) == "a-b-c-d e f ghi"
