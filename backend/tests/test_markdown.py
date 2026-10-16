import pytest

from telereason.ai.markdown import clean_note

# A note from the 11 Oct night, shortened; bold marks were visible in the report.
BOLD_NOTE = (
    "The abnormal-behaviour score is **0.845**. The most deviating signal is "
    "**effort_target_torque_5** (z = -8.36). A technician should look first at "
    "**setpoint_acc_5**."
)


def test_bold_marks_are_removed() -> None:
    assert clean_note(BOLD_NOTE) == (
        "The abnormal-behaviour score is 0.845. The most deviating signal is "
        "effort_target_torque_5 (z = -8.36). A technician should look first at setpoint_acc_5."
    )


def test_underscores_in_channel_names_are_kept() -> None:
    text = "Look first at feedback_tcp_speed_0 vs setpoint_tcp_speed_0 and _x_."

    assert clean_note(text) == text


def test_backticks_and_italics_are_removed() -> None:
    assert clean_note("Check `vibration_1` and *torque_2*.") == "Check vibration_1 and torque_2."


def test_headings_and_bullets_are_removed() -> None:
    text = (
        "## Window summary\n"
        "- vibration_1 is high (z = 3.1).\n"
        "* torque_2 is low.\n"
        "  + current_0 is steady.\n"
        "• Look first at vibration_1."
    )

    assert clean_note(text) == (
        "Window summary vibration_1 is high (z = 3.1). torque_2 is low. current_0 is steady. "
        "Look first at vibration_1."
    )


@pytest.mark.parametrize(
    "text",
    [
        "-8.36 below normal",  # a negative number at the start of a line is not a bullet
        "Signal #2 deviates.",  # '#' inside a line is not a heading
        "The gap rose by 0.01 - well above normal.",
    ],
)
def test_text_that_only_looks_like_markdown_is_kept(text: str) -> None:
    assert clean_note(text) == text


def test_whitespace_collapses_to_single_spaces() -> None:
    assert clean_note("  First line.\n\n Second\tline.  ") == "First line. Second line."


def test_empty_note_stays_empty() -> None:
    assert clean_note("") == ""
    assert clean_note("**\n``") == ""
