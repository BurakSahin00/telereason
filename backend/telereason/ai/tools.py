"""Narrow, read-only tools for the live assistant.

Each tool returns a small JSON result for the model and a one-line summary for the user.
No tool touches equipment and no tool returns raw telemetry: the model sees the gate
verdicts, the morning report, the facts of one window and maintenance protocols.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from telereason.api.schemas import (
    AnswerEvent,
    GateLine,
    GateResponse,
    Line,
    ReportResponse,
    WindowDetail,
)
from telereason.data.loader import WindowNotFoundError, WindowSource
from telereason.data.paths import dataset_dir

logger = logging.getLogger(__name__)

LINES: tuple[Line, ...] = ("FactoryWave", "AURSAD")
KNOWLEDGE_GRAPH = Path("knowledge_graph") / "knowledge_graph.json"
REPORT_TOP = 5
PROTOCOL_HITS = 3
# Scores reach the model with three decimals; the store keeps the full value.
SCORE_DECIMALS = 3

# Fault words that name a cause even when the model paraphrases a root cause. Full root
# cause and error names come from the knowledge graph (`ProtocolIndex.fault_names`).
FAULT_TERMS = (
    "collision",
    "misalignment",
    "misaligned",
    "obstruction",
    "contamination",
    "overload",
    "miscommutation",
    "misconfiguration",
    "misconfigured",
    "singularity",
    "çarpışma",
    "aşırı yük",
    "tıkanma",
    "kirlenme",
)
_STOPWORDS = frozenset(
    "the and for with what how when which should does this that from into about protocol "
    "protocols maintenance robot procedure".split()
)


def normalize(text: str) -> str:
    """Lower case, every run of non-word characters (including `_`) becomes one space."""
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def find_fault_name(text: str, names: frozenset[str]) -> str | None:
    """Return the first fault name (already normalized) that occurs in `text` as words."""
    padded = f" {normalize(text)} "
    return next((name for name in sorted(names) if f" {name} " in padded), None)


# Maintenance protocols (FactoryBench knowledge graph)


@dataclass(frozen=True)
class Protocol:
    protocol_id: int
    fault: str
    error_name: str | None
    description: str
    steps: str


class ProtocolIndex:
    """Keyword search over the knowledge graph's error-to-protocol mapping."""

    def __init__(self, protocols: list[Protocol]) -> None:
        self.protocols = protocols
        self._text = {
            p.protocol_id: normalize(f"{p.fault} {p.error_name or ''}") for p in protocols
        }
        self._body = {p.protocol_id: normalize(f"{p.description} {p.steps}") for p in protocols}

    @classmethod
    def from_knowledge_graph(cls, payload: dict[str, Any]) -> ProtocolIndex:
        protocols = [
            Protocol(
                protocol_id=int(row["fault_id"]),
                fault=str(row["root_cause"]),
                error_name=row.get("ur3_error_name"),
                description=str(row.get("ur3_description") or ""),
                steps=str(row["ur3_protocol"]),
            )
            for row in payload.get("root_cause_error_mapping", [])
            if row.get("ur3_protocol")
        ]
        return cls(protocols)

    @classmethod
    def load(cls, data_dir: str | Path) -> ProtocolIndex:
        """Read the downloaded knowledge graph; an empty index when it is not there."""
        path = dataset_dir(data_dir) / KNOWLEDGE_GRAPH
        if not path.exists():
            logger.warning("knowledge graph not found at %s; protocol search is empty", path)
            return cls([])
        index = cls.from_knowledge_graph(json.loads(path.read_text(encoding="utf-8")))
        logger.info("protocol search: %d protocols from %s", len(index.protocols), path)
        return index

    @property
    def fault_names(self) -> frozenset[str]:
        names = {normalize(p.fault) for p in self.protocols}
        names |= {normalize(p.error_name) for p in self.protocols if p.error_name}
        return frozenset(names | {normalize(term) for term in FAULT_TERMS})

    def search(self, query: str, limit: int = PROTOCOL_HITS) -> list[Protocol]:
        words = {w for w in normalize(query).split() if len(w) > 2 and w not in _STOPWORDS}
        scored = []
        for protocol in self.protocols:
            head = self._text[protocol.protocol_id].split()
            body = self._body[protocol.protocol_id].split()
            score = sum(2 * (w in head) + (w in body) for w in words)
            if score:
                scored.append((score, protocol.protocol_id, protocol))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [protocol for _, _, protocol in scored[:limit]]


# Data the tools read and the result they return


@dataclass(frozen=True)
class AgentData:
    store: WindowSource
    gate: GateResponse
    protocols: ProtocolIndex
    report: ReportResponse | None = None

    def gate_line(self, line: Line) -> GateLine | None:
        return next((row for row in self.gate.lines if row.line == line), None)

    def restricted(self, line: Line) -> bool:
        """True unless the gate opened diagnosis on `line`; a line without a verdict is closed."""
        row = self.gate_line(line)
        return row is None or row.verdict != "open"


@dataclass(frozen=True)
class ToolContext:
    data: AgentData
    window_id: str | None
    restricted: bool


@dataclass(frozen=True)
class ToolResult:
    content: dict[str, Any]
    summary: str
    lines: frozenset[Line] = frozenset()
    window: WindowDetail | None = None
    # Set when the tool result is the final answer, written by code.
    answer: AnswerEvent | None = None
    ok: bool = True
    protocol_ids: tuple[int, ...] = ()


def tool_error(message: str) -> ToolResult:
    return ToolResult(content={"error": message}, summary=message, ok=False)


# Gate and window texts written by code


def _auroc(row: GateLine) -> str:
    low, high = row.auroc_ci
    return f"AUROC {row.auroc:.3f}, interval {low:.3f} to {high:.3f}"


def model_score(score: float | None) -> float | None:
    """A score as the model sees it, in tool results and sentences. Thresholds and flags are
    decided on the stored, unrounded score."""
    return None if score is None else round(score, SCORE_DECIMALS)


def gate_text(row: GateLine) -> str:
    if row.verdict == "closed":
        return f"Diagnosis is closed on {row.line}. {row.explanation} ({_auroc(row)})."
    if row.verdict == "screening":
        return (
            f"{row.line} has abnormal-behaviour screening only; the data does not support "
            f"naming a cause. {row.explanation} ({_auroc(row)})."
        )
    return f"The gate allows diagnosis on {row.line} ({_auroc(row)})."


def window_text(window: WindowDetail, row: GateLine | None) -> str:
    parts = [gate_text(row)] if row else []
    if row is None or row.verdict == "closed":
        parts.append(f"Only verified facts are reported for {window.window_id}.")
    elif window.score is not None:
        flagged = " It is flagged for review." if window.flagged else ""
        parts.append(
            f"The abnormal-behaviour score for {window.window_id} is "
            f"{model_score(window.score)}.{flagged}"
        )
    if window.look_first:
        parts.append(f"Look first at {window.look_first}.")
    parts.extend(f"{fact.signal}: {fact.plain}" for fact in window.facts)
    return " ".join(parts)


def facts_only_answer(window: WindowDetail, row: GateLine | None) -> AnswerEvent:
    return AnswerEvent(answer="FACTS_ONLY", answer_source="tool", text=window_text(window, row))


def _shows_score(data: AgentData, line: Line) -> bool:
    row = data.gate_line(line)
    return row is not None and row.verdict in ("screening", "open")


def _window(ctx: ToolContext, window_id: str | None) -> WindowDetail | ToolResult:
    window_id = window_id or ctx.window_id
    if not window_id:
        return tool_error("no window id given")
    try:
        return ctx.data.store.get_window(window_id)
    except WindowNotFoundError:
        return tool_error(f"window {window_id} does not exist")


# Tools


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportArgs(_Args):
    # The report always covers both lines; a `line` the model still sends is ignored.
    model_config = ConfigDict(extra="ignore")


class ListWindowsArgs(_Args):
    line: Line | None = None
    flagged: bool | None = None
    sort: Literal["score", "deviation"] | None = None
    limit: int = Field(default=5, ge=1, le=10)


class WindowArgs(_Args):
    window_id: str | None = None


class ClassifyArgs(_Args):
    window_id: str | None = None
    line: Line | None = None


class ProtocolArgs(_Args):
    query: str = Field(min_length=2)


def get_report(ctx: ToolContext, args: ReportArgs) -> ToolResult:
    report = ctx.data.report
    if report is None:
        return tool_error("no morning report stored")
    rows = report.lines
    content = {
        "run_id": report.run_id,
        "lines": [
            {
                "line": row.line,
                "verdict": row.verdict,
                "scanned": row.scanned,
                "flagged": row.flagged,
                "threshold": row.threshold,
                "top_windows": [
                    {
                        "window_id": item.window_id,
                        "score": model_score(item.score),
                        "look_first": item.look_first,
                    }
                    for item in row.items[:REPORT_TOP]
                ],
            }
            for row in rows
        ],
    }
    summary = "; ".join(f"{row.line} {row.verdict}, {row.scanned} scanned" for row in rows)
    return ToolResult(
        content, summary or "no line in the report", lines=frozenset(r.line for r in rows)
    )


def list_windows(ctx: ToolContext, args: ListWindowsArgs) -> ToolResult:
    page = ctx.data.store.list_windows(
        line=args.line, flagged=args.flagged, sort=args.sort, page_size=args.limit
    )
    items = [
        {
            "window_id": item.window_id,
            "line": item.line,
            "score": model_score(item.score) if _shows_score(ctx.data, item.line) else None,
            "flagged": item.flagged,
            "look_first": item.look_first,
        }
        for item in page.items
    ]
    lines = {item.line for item in page.items} | ({args.line} if args.line else set())
    content = {"total": page.total, "items": items}
    return ToolResult(content, f"{len(items)} of {page.total} windows", lines=frozenset(lines))


def get_window(ctx: ToolContext, args: WindowArgs) -> ToolResult:
    window = _window(ctx, args.window_id)
    if isinstance(window, ToolResult):
        return window
    row = ctx.data.gate_line(window.line)
    content = {
        "window_id": window.window_id,
        "line": window.line,
        "verdict": row.verdict if row else "closed",
        "score": model_score(window.score) if _shows_score(ctx.data, window.line) else None,
        "flagged": window.flagged,
        "look_first": window.look_first,
        "facts": [
            {"signal": f.signal, "z": f.z, "difference": f.difference, "plain": f.plain}
            for f in window.facts
        ],
    }
    summary = f"{window.window_id}: {len(window.facts)} facts, look first at {window.look_first}"
    return ToolResult(content, summary, lines=frozenset({window.line}), window=window)


def classify_fault(ctx: ToolContext, args: ClassifyArgs) -> ToolResult:
    """The gate decides what may be said about a fault; the answer is written here, in code."""
    window = None
    if args.window_id or (ctx.window_id and not args.line):
        found = _window(ctx, args.window_id)
        if isinstance(found, ToolResult):
            return found
        window = found
    line = window.line if window else args.line
    rows = [r for r in ctx.data.gate.lines if line in (None, r.line)]
    if not rows:
        return tool_error(f"no gate verdict for {line}")
    if window is not None:
        text = window_text(window, rows[0])
    else:
        text = " ".join(gate_text(row) for row in rows)
    verdicts = {row.verdict for row in rows}
    if verdicts == {"screening"}:
        code, summary = "SCREENING_ONLY", "screening only on this line"
    elif "open" not in verdicts:
        code, summary = "DIAGNOSIS_CLOSED", "diagnosis closed on this line"
    else:
        code, summary = "NOT_ANSWERABLE", "no fault classifier for this line"
        text = f"{text} The assistant has no fault classifier, so it does not name a cause."
    content = {"lines": [{"line": r.line, "verdict": r.verdict} for r in rows]}
    return ToolResult(
        content,
        summary,
        lines=frozenset(r.line for r in rows),
        window=window,
        answer=AnswerEvent(answer=code, answer_source="tool", text=text),
    )


def search_protocols(ctx: ToolContext, args: ProtocolArgs) -> ToolResult:
    if ctx.restricted:
        # A protocol is chosen by its fault; on a closed or screening line that would name one.
        return tool_error("protocol lookup by fault is not available: no diagnosis on this line")
    hits = ctx.data.protocols.search(args.query)
    content = {
        "protocols": [
            {
                "protocol_id": p.protocol_id,
                "fault": p.fault,
                "error": p.error_name,
                "steps": p.steps,
            }
            for p in hits
        ]
    }
    # The summary is shown to the user before the guard runs, so it names no fault.
    ids = tuple(p.protocol_id for p in hits)
    return ToolResult(content, f"{len(hits)} matching protocols", protocol_ids=ids)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    params: str
    args: type[_Args]
    run: Callable[[ToolContext, Any], ToolResult]


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool(
            "get_report",
            "Last night's morning report for both lines: verdict, scanned and flagged counts "
            "and the top windows per line.",
            "",
            ReportArgs,
            get_report,
        ),
        Tool(
            "list_windows",
            "List scanned windows. Scores exist only on screening lines.",
            'line?: "FactoryWave" | "AURSAD",\nflagged?: boolean,\n'
            'sort?: "score" | "deviation",\n// 1 to 10\nlimit?: number, // default: 5',
            ListWindowsArgs,
            list_windows,
        ),
        Tool(
            "get_window",
            "Verified facts of one window: deviating signals, z, difference, look-first signal.",
            "// Defaults to the window the user is looking at\nwindow_id?: string,",
            WindowArgs,
            get_window,
        ),
        Tool(
            "classify_fault",
            "Call for any question about a fault, its cause or what went wrong. Returns what "
            "the gate allows to be said for the window or line.",
            'window_id?: string,\nline?: "FactoryWave" | "AURSAD",',
            ClassifyArgs,
            classify_fault,
        ),
        Tool(
            "search_protocols",
            "Search maintenance protocols by error, symptom or fault keywords.",
            "query: string,",
            ProtocolArgs,
            search_protocols,
        ),
    )
}


def run_tool(ctx: ToolContext, name: str, arguments: dict[str, Any]) -> ToolResult:
    tool = TOOLS.get(name)
    if tool is None:
        return tool_error(f"unknown tool {name}")
    try:
        args = tool.args.model_validate(arguments)
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, err["loc"])) or "arguments" for err in exc.errors())
        return tool_error(f"invalid arguments for {name}: {fields}")
    return tool.run(ctx, args)


def render_tools() -> str:
    """The `functions` namespace of the harmony developer message."""
    blocks = [
        f"// {tool.description}\ntype {tool.name} = "
        + (f"(_: {{\n{tool.params}\n}})" if tool.params else "()")
        + " => any;"
        for tool in TOOLS.values()
    ]
    return (
        "## functions\n\nnamespace functions {\n\n"
        + "\n\n".join(blocks)
        + "\n\n} // namespace functions"
    )
