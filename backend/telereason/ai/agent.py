"""Live assistant: a harmony tool loop over the narrow tools in `tools.py`.

gpt-oss writes harmony messages. Tool calls go to the commentary channel with
`to=functions.<name>`, the answer to the final channel, and its reasoning to the analysis
channel, which is never shown to the user. The loop allows at most `MAX_TOOL_STEPS` tool
calls; the next turn is forced onto the final channel.

The product rules are enforced here in code, not only in the prompt: action requests are
refused before the model runs, fault questions are answered by `classify_fault` in code,
and a model answer that names a fault on a closed or screening line is replaced by the
verified facts.

`answer_source` is `model` when the language model wrote the answer text and `tool` when
code wrote it: from a tool result, a refusal or the not-answerable text.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from telereason.ai.engine import Engine, Priority
from telereason.ai.tools import (
    LINES,
    AgentData,
    ToolContext,
    ToolResult,
    facts_only_answer,
    find_fault_name,
    gate_text,
    render_tools,
    run_tool,
)
from telereason.api.schemas import (
    AnswerEvent,
    AnswerMessage,
    DoneEvent,
    DoneMessage,
    Line,
    ToolCallEvent,
    ToolCallMessage,
    ToolResultEvent,
    ToolResultMessage,
    WindowDetail,
)
from telereason.data.loader import WindowNotFoundError

logger = logging.getLogger(__name__)

MAX_TOOL_STEPS = 4
MAX_TURNS = MAX_TOOL_STEPS + 1  # the last turn is forced onto the final channel
TURN_MAX_TOKENS = 1024
STOP = ("<|call|>", "<|return|>")
NOT_ANSWERABLE = "NOT_ANSWERABLE"

NOT_ANSWERABLE_TEXT = "I cannot answer this question with the data."
REFUSAL_TEXT = (
    "I cannot start, stop or change any equipment. That decision stays with the technician; "
    "I can show what last night's scan found to help you decide."
)

SYSTEM = (
    "<|start|>system<|message|>You are ChatGPT, a large language model trained by OpenAI.\n\n"
    "Reasoning: low\n\n"
    "# Valid channels: analysis, commentary, final. Channel must be included for every "
    "message.\nCalls to these tools must go to the commentary channel: 'functions'.<|end|>"
)
INSTRUCTIONS = f"""You are TeleReason, the maintenance assistant for robot-cell technicians. \
You answer questions about last night's morning report, the scanned windows, the gate \
verdicts and maintenance protocols.

- Take every fact from a tool. Never invent numbers, signals, window ids or causes; quote \
numbers exactly as the tools give them.
- You cannot operate equipment. If asked to start, stop, reset, move or change a machine, \
refuse in one sentence and say the decision stays with the technician.
- For any question about a fault, its cause or what went wrong, call classify_fault.
- On a line with verdict closed, never name a fault or a cause: give verified facts only. \
On a line with verdict screening, give only the abnormal-behaviour score, never a cause.
- If the tools cannot answer the question, or it is not about these robot cells, reply \
with exactly {NOT_ANSWERABLE}.
- Use at most {MAX_TOOL_STEPS} tool calls. Answer briefly, in the language of the question."""

# Imperative requests to operate equipment, in English and Turkish. "Why did the robot
# stop?" is a question, not a request, so English verbs count only at the start of a
# request; Turkish verbs only with an imperative or request ending.
_EN_VERBS = (
    r"stop|halt|start|restart|reboot|shut\s*down|turn\s+(?:on|off)|power\s+(?:on|off|down)|"
    r"switch\s+(?:on|off)|reset|move|jog|resume|pause|disable|enable|override|"
    r"change|adjust|set|increase|decrease|lower|raise|run"
)
_EN_ACTION = re.compile(
    rf"(?:^|[.!?;]\s*|\b(?:please|you\s+to|go\s+ahead\s+and)\s+|"
    rf"\b(?:can|could|would|will)\s+you\s+(?:please\s+)?)(?:{_EN_VERBS})\b",
    re.IGNORECASE,
)
_TR_STEMS = (
    r"durdur|başlat|yeniden\s+başlat|kapat|sıfırla|resetle|çalıştır|duraklat|"
    r"hareket\s+ettir|devre\s+dışı\s+bırak|etkinleştir|değiştir|ayarla|artır|azalt"
)
_TR_ACTION = re.compile(
    rf"\b(?:{_TR_STEMS})(?:$|[\s.!?,]|[ıiuü]n(?:[ıiuü]z)?\b|[ıiuüae]r\s+m[ıiuü]s[ıiuü]n|"
    rf"y?[ae]bilir\s+m[ıiuü]|m[ae]n[ıi]\s+ist)",
    re.IGNORECASE,
)


def is_action_request(question: str) -> bool:
    text = question.strip()
    return bool(_EN_ACTION.search(text) or _TR_ACTION.search(text))


# A harmony special token in the question would open a message of its own (prompt injection).
_HARMONY_MARKER = re.compile(r"<\|.*?\|>", re.DOTALL)


def strip_harmony_markers(text: str) -> str:
    """Remove every `<|...|>` string; repeat until none is left, so markers cannot nest."""
    while (cleaned := _HARMONY_MARKER.sub("", text)) != text:
        text = cleaned
    return text


# Harmony


@dataclass(frozen=True)
class HarmonyMessage:
    channel: str | None
    recipient: str | None
    content: str


_CHANNEL = re.compile(r"<\|channel\|>\s*(\w+)")
_RECIPIENT = re.compile(r"\bto=([\w.\-]+)")
_END = re.compile(r"<\|(?:end|call|return)\|>")


def parse_harmony(text: str) -> list[HarmonyMessage]:
    """Split one completion into messages. The recipient may stand before or after the channel."""
    messages = []
    for chunk in text.split("<|start|>"):
        header, found, body = chunk.partition("<|message|>")
        if not found:
            continue
        channel = _CHANNEL.search(header)
        recipient = _RECIPIENT.search(header)
        messages.append(
            HarmonyMessage(
                channel=channel.group(1) if channel else None,
                recipient=recipient.group(1) if recipient else None,
                content=_END.split(body, maxsplit=1)[0].strip(),
            )
        )
    return messages


def parse_arguments(content: str) -> dict[str, Any] | None:
    """JSON object arguments of a tool call; `None` when they cannot be read."""
    if not content:
        return {}
    match = re.search(r"\{.*\}", content, re.DOTALL)
    try:
        value = json.loads(match.group(0) if match else content)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _assistant(channel: str, content: str) -> str:
    return f"<|start|>assistant<|channel|>{channel}<|message|>{content}<|end|>"


def _tool_call(name: str, arguments: str) -> str:
    return (
        f"<|start|>assistant<|channel|>commentary to=functions.{name} <|constrain|>json"
        f"<|message|>{arguments}<|call|>"
    )


def _tool_output(name: str, content: dict[str, Any]) -> str:
    body = json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    return f"<|start|>functions.{name} to=assistant<|channel|>commentary<|message|>{body}<|end|>"


def _developer(window_id: str | None) -> str:
    context = f"\n\nThe user is looking at window {window_id}." if window_id else ""
    return (
        f"<|start|>developer<|message|># Instructions\n\n{INSTRUCTIONS}{context}\n\n"
        f"# Tools\n\n{render_tools()}<|end|>"
    )


def _user(question: str) -> str:
    return f"<|start|>user<|message|>{question}<|end|>"


WARMUP_QUESTION = "Which windows did last night's scan flag?"


def warm_up_prompt() -> str:
    """The first-turn prompt of a real question. Warming up with a prompt of this size
    compiles the GPU kernels for it, so the first technician's question does not wait."""
    return SYSTEM + _developer(None) + _user(WARMUP_QUESTION) + "<|start|>assistant"


# The loop


AgentMessage = ToolCallMessage | ToolResultMessage | AnswerMessage | DoneMessage


@dataclass
class _Session:
    data: AgentData
    window_id: str | None
    restricted: set[Line]
    protocol_lookup: bool = False
    window: WindowDetail | None = None

    def touch(self, result: ToolResult) -> None:
        self.restricted |= {line for line in result.lines if self.data.restricted(line)}
        self.window = result.window or self.window


def _start_session(data: AgentData, question: str, window_id: str | None) -> _Session:
    """Lines named in the question or owning the user's window count as touched from the start."""
    folded = question.casefold()
    restricted = {line for line in LINES if line.casefold() in folded and data.restricted(line)}
    session = _Session(data=data, window_id=window_id, restricted=restricted)
    if window_id:
        try:
            window = data.store.get_window(window_id)
        except WindowNotFoundError:
            logger.info("ask: window %s does not exist", window_id)
        else:
            if data.restricted(window.line):
                session.restricted.add(window.line)
    return session


def _not_answerable() -> AnswerEvent:
    return AnswerEvent(answer=NOT_ANSWERABLE, answer_source="tool", text=NOT_ANSWERABLE_TEXT)


def _final_answer(session: _Session, text: str) -> AnswerEvent:
    if not text or text.startswith(NOT_ANSWERABLE):
        return _not_answerable()
    # Fault names are allowed only in a protocol answer that touched no closed or screening line.
    guarded = bool(session.restricted) or not session.protocol_lookup
    fault = find_fault_name(text, session.data.protocols.fault_names) if guarded else None
    if fault is None:
        return AnswerEvent(answer="ANSWERED", answer_source="model", text=text)
    logger.warning("ask: model answer named a fault (%s); replaced by facts", fault)
    if session.window is not None:
        return facts_only_answer(session.window, session.data.gate_line(session.window.line))
    rows = [row for line in sorted(session.restricted) if (row := session.data.gate_line(line))]
    if rows:
        text = " ".join(gate_text(row) for row in rows)
        return AnswerEvent(answer="FACTS_ONLY", answer_source="tool", text=text)
    return _not_answerable()


async def ask(
    engine: Engine, data: AgentData, question: str, window_id: str | None = None
) -> AsyncIterator[AgentMessage]:
    """Answer one question; yields `tool_call`, `tool_result`, `answer` and `done` in order."""
    started = time.perf_counter()
    tokens = 0

    def done() -> DoneMessage:
        total_ms = round((time.perf_counter() - started) * 1000)
        return DoneMessage(event="done", data=DoneEvent(total_ms=total_ms, tokens=tokens))

    question = strip_harmony_markers(question).strip()
    if not question:
        yield AnswerMessage(event="answer", data=_not_answerable())
        yield done()
        return

    if is_action_request(question):
        refusal = AnswerEvent(answer="ACTION_REFUSED", answer_source="tool", text=REFUSAL_TEXT)
        yield AnswerMessage(event="answer", data=refusal)
        yield done()
        return

    session = _start_session(data, question, window_id)
    conversation = [SYSTEM, _developer(window_id), _user(question)]
    steps = 0
    force_final = False
    answer: AnswerEvent | None = None

    for turn in range(1, MAX_TURNS + 1):
        forced = force_final or steps >= MAX_TOOL_STEPS or turn == MAX_TURNS
        prefix = "<|start|>assistant" + ("<|channel|>final<|message|>" if forced else "")
        generation = await engine.generate(
            "".join(conversation) + prefix,
            priority=Priority.LIVE,
            max_tokens=TURN_MAX_TOKENS,
            stop=STOP,
            kind="agent",
        )
        tokens += generation.completion_tokens
        text = generation.text
        if forced and not text.lstrip().startswith("<|channel|>"):
            text = "<|channel|>final<|message|>" + text
        messages = parse_harmony(text)

        call = next((m for m in messages if (m.recipient or "").startswith("functions.")), None)
        final = next((m for m in messages if m.channel == "final" and m.recipient is None), None)
        if forced or call is None:
            if final is not None or forced:
                answer = _final_answer(session, final.content if final else "")
                break
            # Analysis only, e.g. cut off by the token limit: the same prompt would give the
            # same text again, so keep the analysis and force the final channel next.
            conversation += [_assistant("analysis", m.content) for m in messages]
            force_final = True
            continue

        steps += 1
        name = call.recipient.removeprefix("functions.") if call.recipient else ""
        arguments = parse_arguments(call.content)
        yield ToolCallMessage(
            event="tool_call", data=ToolCallEvent(turn=turn, tool=name, args=arguments or {})
        )
        tool_started = time.perf_counter()
        if arguments is None:
            result = ToolResult(
                {"error": "arguments are not a JSON object"}, "invalid arguments", ok=False
            )
        else:
            context = ToolContext(data, window_id, restricted=bool(session.restricted))
            result = run_tool(context, name, arguments)
        ms = round((time.perf_counter() - tool_started) * 1000)
        yield ToolResultMessage(
            event="tool_result",
            data=ToolResultEvent(turn=turn, tool=name, summary=result.summary, ms=ms),
        )
        session.touch(result)
        session.protocol_lookup |= name == "search_protocols" and result.ok
        if result.answer is not None:
            answer = result.answer
            break
        # The analysis before a tool call stays in the conversation for the next turn.
        conversation += [
            _assistant("analysis", m.content) for m in messages if m.channel == "analysis"
        ]
        conversation += [_tool_call(name, call.content), _tool_output(name, result.content)]

    yield AnswerMessage(event="answer", data=answer or _not_answerable())
    yield done()
