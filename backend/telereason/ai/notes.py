"""Night notes: two or three sentences per window, written by the LLM from code facts.

The night run builds the facts with code and passes them as one JSON object, the 11 Oct
input format (`json.dumps` of `telereason.night.facts.WindowFacts.note_input`). The model
gets fixed instructions for the line's gate mode, writes on the final channel, and the
verifier checks the cleaned text. A note that does not pass is written once more with
another seed; if that one fails too, the window keeps its facts and shows no note.
"""

import logging
import zlib
from dataclasses import dataclass
from typing import Literal

from telereason.ai.agent import parse_harmony
from telereason.ai.engine import HARMONY_SYSTEM, Engine, Generation, Priority
from telereason.ai.markdown import clean_note
from telereason.ai.verifier import Check, ForbiddenTerms, verify
from telereason.api.schemas import Verdict

logger = logging.getLogger(__name__)

NoteMode = Literal["closed", "screening"]

NOTE_MAX_TOKENS = 1024
NOTE_TEMPERATURE = 1.0
NOTE_ATTEMPTS = 2
NOTE_STOP = ("<|call|>", "<|return|>")

# Instructions from the 11 Oct night (v2), unchanged: the verifier pass rate was measured
# with exactly these texts.
_COMMON = (
    "Use only the facts in the JSON. Copy every number exactly as it appears in the JSON; do "
    "not compute new numbers, percentages or ratios, and do not write the window identifier. "
    "Say that a technician should look first at the signal named in look_first (if look_first "
    "is null, say that no signal stands out). Do not name, guess or hint at a fault type or a "
    "cause, and do not use the word 'cause'."
)
INSTRUCTIONS: dict[NoteMode, str] = {
    "closed": (
        "You write one entry for the night-shift maintenance report about one telemetry window "
        "of a robot cell. The fault-diagnosis gate for this line is CLOSED: on new recording "
        "days the fault classifier is no better than knowing the task, so no fault type or "
        "cause may be stated. In 2-3 short sentences, say which signals deviate most from "
        "normal operation of the same task, using their z values and the differences given in "
        "the JSON. " + _COMMON
    ),
    "screening": (
        "You write one entry for the night-shift maintenance report about one telemetry window "
        "of a robot cell. On this line the gate allows only normal-versus-abnormal screening; "
        "the cause of a fault may not be stated. In 2-3 short sentences, give the "
        "abnormal-behaviour score exactly as in the JSON and say which signals deviate most "
        "from normal operation, using their z values and the differences given in the JSON. "
        + _COMMON
    ),
}


def note_mode(verdict: Verdict) -> NoteMode:
    """`closed` lines get facts only; `screening` and `open` lines also get the score."""
    return "closed" if verdict == "closed" else "screening"


# Generation


def note_prompt(facts_json: str, mode: NoteMode) -> str:
    return (
        f"{HARMONY_SYSTEM}<|start|>developer<|message|># Instructions\n\n{INSTRUCTIONS[mode]}"
        f"<|end|><|start|>user<|message|>{facts_json}<|end|><|start|>assistant"
    )


def note_seed(window_id: str, attempt: int) -> int:
    """Seed tied to the window, different for the retry; reruns give the same notes."""
    return zlib.crc32(f"{window_id}|{attempt}".encode()) & 0x7FFFFFFF


def final_text(text: str) -> str | None:
    """Content of the last final-channel message, or None when the model never got there."""
    finals = [m for m in parse_harmony(text) if m.channel == "final" and m.recipient is None]
    return finals[-1].content if finals else None


@dataclass(frozen=True)
class NoteAttempt:
    seed: int
    raw: str
    text: str
    check: Check
    cut_off: bool
    completion_tokens: int
    elapsed_ms: int

    @property
    def passed(self) -> bool:
        return self.check.passed


@dataclass(frozen=True)
class NoteResult:
    window_id: str
    attempts: tuple[NoteAttempt, ...]

    @property
    def first_passed(self) -> bool:
        return self.attempts[0].passed

    @property
    def verified(self) -> bool:
        return self.attempts[-1].passed

    @property
    def note(self) -> str:
        """The text shown in the report: the last attempt if it passed, otherwise empty."""
        return self.attempts[-1].text if self.verified else ""


def _attempt(
    generation: Generation, seed: int, facts_json: str, terms: ForbiddenTerms
) -> NoteAttempt:
    cut_off = generation.completion_tokens >= NOTE_MAX_TOKENS
    content = None if cut_off else final_text(generation.text)
    text = clean_note(content) if content is not None else ""
    check = verify(text, facts_json, terms) if content is not None else Check(present=False)
    return NoteAttempt(
        seed=seed,
        raw=generation.text,
        text=text,
        check=check,
        cut_off=cut_off,
        completion_tokens=generation.completion_tokens,
        elapsed_ms=generation.elapsed_ms,
    )


async def write_note(
    engine: Engine,
    facts_json: str,
    *,
    window_id: str,
    mode: NoteMode,
    terms: ForbiddenTerms,
) -> NoteResult:
    """Write and verify one note; retry once with another seed if it does not pass."""
    prompt = note_prompt(facts_json, mode)
    attempts: list[NoteAttempt] = []
    for number in range(NOTE_ATTEMPTS):
        seed = note_seed(window_id, number)
        generation = await engine.generate(
            prompt,
            priority=Priority.NIGHT,
            max_tokens=NOTE_MAX_TOKENS,
            temperature=NOTE_TEMPERATURE,
            stop=NOTE_STOP,
            kind="note",
            seed=seed,
        )
        attempts.append(_attempt(generation, seed, facts_json, terms))
        if attempts[-1].passed:
            break
        check = attempts[-1].check
        logger.info(
            "note for %s failed attempt %d (final=%s, cut_off=%s, unsourced=%s, violations=%s)",
            window_id,
            number + 1,
            check.present,
            attempts[-1].cut_off,
            list(check.unsourced),
            list(check.violations),
        )
    return NoteResult(window_id=window_id, attempts=tuple(attempts))
