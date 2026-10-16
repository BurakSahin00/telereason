"""Code check for night notes: every number comes from the facts, no fault is named.

The rules are the ones measured on the 11 Oct MI300X night (7,422 notes; 7,421 passed on the
first attempt, the last one after the retry):

1. Unicode dashes, special spaces and invisible characters are flattened first, so a
   window id written with U+2011 is still read as one identifier.
2. Identifiers that mix letters with digits or underscores (channel names, window ids)
   are removed before numbers are read.
3. Every number must match a number in the facts JSON: equal after rounding to the
   note's decimals, or within 1 %. Percentages are tried as written and divided by 100.
   Whole numbers from 0 to 3 are always allowed ("two signals", "3 windows").
4. Forbidden: a fault class name (with underscores or spaces), a distinctive word of five
   or more letters taken from a class name unless the facts JSON contains it, and the
   phrases in `FORBIDDEN_PHRASES`.

The check reads where numbers come from and which words are used, not what the note
means: passing does not make a note correct.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

_DASHES = dict.fromkeys(map(ord, "‐‑‒–—―−﹘﹣－"), "-")
_SPACES = dict.fromkeys(
    map(
        ord,
        "            　",
    ),
    " ",
)
_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"), None)
_FLATTEN = {**_DASHES, **_SPACES, **_INVISIBLE}

_IDENTIFIER = re.compile(
    r"(?<![\d.])[A-Za-z_][A-Za-z0-9_\-]*\d[A-Za-z0-9_\-]*|[A-Za-z]+_[A-Za-z0-9_]+"
)
_NUMBER = re.compile(r"(?<![\w.])[-+]?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?(?:\s*%)?")
_WORD = re.compile(r"[a-z]+")

FORBIDDEN_PHRASES = (
    "root cause",
    "caused by",
    "likely cause",
    "probable cause",
    "possible cause",
)
# Words in class names that also describe plain signal behaviour; they stay allowed.
STOP_WORDS = frozenset(
    {
        "normal",
        "phase",
        "object",
        "objects",
        "component",
        "components",
        "extra",
        "additional",
        "external",
        "missing",
        "limit",
        "limits",
        "violation",
        "position",
        "positions",
        "joint",
        "joints",
        "plate",
        "axis",
        "link",
        "self",
        "unstable",
        "during",
        "motion",
    }
)
RELATIVE_TOLERANCE = 0.01
FREE_INTEGER_MAX = 3
MAX_LISTED = 10


def flatten_unicode(text: str) -> str:
    """Map Unicode dashes to '-', special spaces to ' ' and drop invisible characters."""
    return text.translate(_FLATTEN)


@dataclass(frozen=True)
class ForbiddenTerms:
    names: tuple[str, ...]
    words: frozenset[str]


def forbidden_terms(classes: Iterable[str]) -> ForbiddenTerms:
    """Class names in both spellings and their distinctive words; `normal` is not a fault."""
    names = [str(c).strip().lower() for c in classes if str(c).strip().lower() != "normal"]
    spellings = sorted({s for name in names for s in (name, name.replace("_", " "))})
    words = frozenset(
        w
        for name in names
        for w in re.split(r"[_\W]+", name)
        if len(w) >= 5 and w not in STOP_WORDS
    )
    return ForbiddenTerms(names=tuple(spellings), words=words)


@dataclass(frozen=True)
class Check:
    present: bool
    unsourced: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()

    @property
    def numbers_ok(self) -> bool:
        return self.present and not self.unsourced

    @property
    def passed(self) -> bool:
        return self.present and not self.unsourced and not self.violations


def json_numbers(facts_json: str) -> list[float]:
    """Every number in the facts JSON, booleans and nulls left out."""
    numbers: list[float] = []

    def walk(value: Any) -> None:
        if isinstance(value, bool) or value is None:
            return
        if isinstance(value, int | float):
            numbers.append(float(value))
        elif isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(json.loads(facts_json))
    return numbers


def number_is_sourced(token: str, numbers: list[float]) -> bool:
    """Whether a number in the note is a number from the facts, compared by absolute value."""
    percent = "%" in token
    text = token.replace("%", "").strip().replace(",", ".")
    try:
        value = abs(float(text))
    except ValueError:
        return True
    scientific = bool(re.search(r"[eE]", text))
    mantissa = re.split(r"[eE]", text)[0]
    decimals = len(mantissa.split(".")[1]) if "." in mantissa else 0
    if not scientific and decimals == 0 and value == int(value) and value <= FREE_INTEGER_MAX:
        return True

    candidates = [(value, decimals)]
    if percent:
        candidates.append((value / 100, decimals + 2))
    for source in numbers:
        source = abs(source)
        for candidate, places in candidates:
            if abs(candidate - source) <= RELATIVE_TOLERANCE * max(source, 1e-12):
                return True
            if not scientific and abs(candidate - source) <= 0.5 * 10 ** (-places) + 1e-9:
                return True
    return False


def verify(text: str | None, facts_json: str, terms: ForbiddenTerms) -> Check:
    """Check one note (the cleaned text the report shows) against its facts JSON."""
    if not text or not text.strip():
        return Check(present=False)
    text = flatten_unicode(text)
    numbers = json_numbers(facts_json)
    without_ids = _IDENTIFIER.sub(" ", text)
    unsourced = [
        token.strip()
        for token in _NUMBER.findall(without_ids)
        if not number_is_sourced(token, numbers)
    ]

    lower = text.lower()
    violations = [name for name in terms.names if name in lower]
    violations += [phrase for phrase in FORBIDDEN_PHRASES if phrase in lower]
    note_words = set(_WORD.findall(lower))
    facts_words = set(_WORD.findall(facts_json.lower()))
    for word in sorted(terms.words - facts_words):
        if word in note_words or f"{word}s" in note_words or f"{word}es" in note_words:
            violations.append(word)

    return Check(
        present=True,
        unsourced=tuple(unsourced[:MAX_LISTED]),
        violations=tuple(sorted(set(violations))),
    )
