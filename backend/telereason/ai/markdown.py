"""Remove markdown marks from night notes, so the report shows plain text.

The model sometimes writes `**bold**`, backticks, headings or bullet lists. The report
and the UI show notes as plain text, so these marks are removed. Underscores are kept:
they are part of channel names such as `setpoint_acc_5`.

The verifier checks the cleaned text, which is the text the technician sees.
"""

import re

_HEADING = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
_BULLET = re.compile(r"^[ \t]*[-+•][ \t]+", re.MULTILINE)
_MARKS = re.compile(r"[*`]")
_WHITESPACE = re.compile(r"\s+")


def clean_note(text: str) -> str:
    """Drop headings, bullets, `*` and backticks; collapse whitespace to single spaces."""
    text = _HEADING.sub("", text)
    text = _BULLET.sub("", text)
    text = _MARKS.sub("", text)
    return _WHITESPACE.sub(" ", text).strip()
