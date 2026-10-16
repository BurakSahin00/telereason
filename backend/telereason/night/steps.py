"""The real night steps (#40): gate, facts, scan, report, in the order of the 11 Oct night.

- **gate**: the gate verdict per line and the score of every window it tested, from the gate
  process or, on a small night, from the latest stored gate (`night/gate_run.py`).
- **facts**: the night pool and its facts (`telereason.night.facts`), loaded once per process
  and shared with `/signals`. The run keeps the windows the gate tested, writes their facts and
  scores to the store, and counts them as progress.
- **scan**: one note per window (`telereason.ai.notes.write_note`, low priority, at most
  `SCAN_CONCURRENCY` at a time) from the window's note input, checked by the verifier against
  the gate's class labels and the knowledge graph's fault names.
- **report**: the morning report from the stored windows and notes (`night/report.py`).

Live questions keep their priority over night notes in the engine, so the assistant answers
while the scan runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zlib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace

from telereason.ai.notes import NoteResult, note_mode, write_note
from telereason.ai.tools import ProtocolIndex
from telereason.ai.verifier import ForbiddenTerms, forbidden_terms
from telereason.api.schemas import Verdict
from telereason.night.band import NightSignals
from telereason.night.facts import WindowFacts
from telereason.night.gate_run import (
    GateOutput,
    GateProcess,
    gate_process,
    read_gate_files,
    reusable_gate,
    run_dir,
)
from telereason.night.report import flagged, has_score, write_report
from telereason.night.run import RunContext
from telereason.night.windows import Window
from telereason.settings import GateMode
from telereason.store.db import NoteRecord, WindowRecord

logger = logging.getLogger(__name__)

SCAN_CONCURRENCY = 64
# Notes go to the store in batches, at most this often, so the scan does not write per note.
NOTE_FLUSH_S = 1.0


@dataclass(frozen=True)
class NightWindow:
    """A window picked for the scan, with what the scan needs."""

    facts: WindowFacts
    verdict: Verdict
    score: float | None


def night_terms(classes: Iterable[str], fault_names: Iterable[str]) -> ForbiddenTerms:
    """Forbidden in a note: the gate's class labels with their distinctive words, and the
    knowledge graph's fault names as whole names, with spaces and with underscores."""
    labels = forbidden_terms(classes)
    names = {str(name).strip().lower() for name in fault_names if str(name).strip()}
    spellings = {
        spelling
        for name in names
        for spelling in (name, name.replace(" ", "_"), name.replace("_", " "))
    }
    return ForbiddenTerms(names=tuple(sorted(set(labels.names) | spellings)), words=labels.words)


def note_records(result: NoteResult) -> list[NoteRecord]:
    records = []
    for number, attempt in enumerate(result.attempts, start=1):
        problems = [f"unsourced number {token}" for token in attempt.check.unsourced]
        problems += list(attempt.check.violations)
        if attempt.cut_off:
            problems.append("cut off at the token limit")
        elif not attempt.check.present:
            problems.append("no final message")
        records.append(
            NoteRecord(
                window_id=result.window_id,
                attempt=number,
                text_raw=attempt.raw,
                text=attempt.text,
                passed=attempt.passed,
                violations=tuple(problems),
                completion_tokens=attempt.completion_tokens,
                ms=attempt.elapsed_ms,
            )
        )
    return records


def pick_windows(windows: Sequence[Window], max_windows: int, run_id: str) -> list[Window]:
    """The windows a run scans (#41), in night order.

    A night of `max_windows` (the demo button asks for 300) keeps the lines in the proportion
    the gate tested them; within a line the windows are drawn in the order of
    `crc32(run_id + window_id)`, so one run id always picks the same windows and the next run
    picks others. A line with too few windows leaves its share to the other line.
    """
    if max_windows >= len(windows):
        return list(windows)
    by_line: dict[str, list[Window]] = {}
    for window in windows:
        by_line.setdefault(window.line, []).append(window)
    shares = {line: max_windows * len(ws) // len(windows) for line, ws in by_line.items()}
    # Hand out what the rounding left, largest line first.
    for line in sorted(by_line, key=lambda line: -len(by_line[line])):
        free = max_windows - sum(shares.values())
        shares[line] = min(len(by_line[line]), shares[line] + max(free, 0))
    picked: set[str] = set()
    for line, line_windows in by_line.items():
        ranked = sorted(line_windows, key=lambda w: zlib.crc32(f"{run_id}{w.window_id}".encode()))
        picked.update(w.window_id for w in ranked[: shares[line]])
    return [w for w in windows if w.window_id in picked]


class NightPipeline:
    """`NightSteps` for real mode: the night's data comes from `signals` (the pool)."""

    def __init__(
        self,
        signals: NightSignals,
        protocols: Callable[[], ProtocolIndex],
        *,
        gate_mode: GateMode = "auto",
        gate_process: GateProcess = gate_process,
        concurrency: int = SCAN_CONCURRENCY,
    ) -> None:
        self.signals = signals
        self.protocols = protocols
        self.gate_mode = gate_mode
        self.gate_process = gate_process
        self.concurrency = concurrency

    async def gate(self, ctx: RunContext) -> None:
        output = None
        if self.gate_mode != "full":
            reused = await asyncio.to_thread(reusable_gate, ctx.db)
            if reused is not None and ctx.max_windows < len(reused.windows):
                logger.info(
                    "night run %s: small night (%d of %d windows), gate reused from run %s",
                    ctx.run_id,
                    ctx.max_windows,
                    len(reused.windows),
                    reused.source_run,
                )
                output = reused
        if output is None:
            out = run_dir(ctx.db, ctx.run_id)
            logger.info("night run %s: running the gate into %s", ctx.run_id, out)
            await self.gate_process(out, str(self.signals.data_dir))
            output = await asyncio.to_thread(read_gate_files, out, ctx.run_id)
        verdicts = output.verdicts.model_copy(update={"run_id": ctx.run_id})
        ctx.db.update_run(ctx.run_id, gate=verdicts)
        ctx.state["gate"] = replace(output, verdicts=verdicts)

    async def facts(self, ctx: RunContext) -> None:
        gate: GateOutput = ctx.state["gate"]
        # Built once per process; the app starts building it at startup.
        night = await asyncio.to_thread(self.signals.load)
        tested = [w for w in night.pool.windows if w.window_id in gate.windows]
        picked = pick_windows(tested, ctx.max_windows, ctx.run_id)
        ctx.progress(0, len(picked), flush=True)
        verdicts: dict[str, Verdict] = {row.line: row.verdict for row in gate.verdicts.lines}

        records, scan = [], []
        for seq, window in enumerate(picked):
            facts = night.facts[window.window_id]
            verdict = verdicts.get(window.line, "closed")
            scored = has_score(verdict)
            tested_window = gate.windows[window.window_id]
            score = tested_window.score if scored else None
            records.append(
                WindowRecord(
                    window_id=window.window_id,
                    line=window.line,
                    task=window.task,
                    seq=seq,
                    ms_id=window.ms_id,
                    group_id=window.group,
                    fold=tested_window.fold,
                    score=score,
                    flagged=flagged(score),
                    max_abs_z=facts.max_abs_z,
                    look_first=facts.look_first or "",
                    facts=tuple(facts.fact_rows()),
                    top_features=tested_window.top_features if scored else (),
                )
            )
            scan.append(NightWindow(facts=facts, verdict=verdict, score=score))
        ctx.db.put_windows(ctx.run_id, records)
        ctx.state["scan"] = scan
        # The gate's class labels; the pool's labels are the same classes when it lists none.
        ctx.state["classes"] = list(gate.classes) or sorted({w.label for w in night.pool.windows})
        ctx.progress(len(picked), flush=True)

    async def scan(self, ctx: RunContext) -> None:
        windows: list[NightWindow] = ctx.state["scan"]
        terms = night_terms(ctx.state["classes"], self.protocols().fault_names)
        ctx.progress(0, len(windows), flush=True)
        limit = asyncio.Semaphore(self.concurrency)
        pending: list[NoteRecord] = []
        flushed_at = time.monotonic()
        done = 0

        def flush() -> None:
            nonlocal flushed_at
            if pending:
                ctx.db.put_notes(ctx.run_id, pending)
                pending.clear()
            flushed_at = time.monotonic()

        async def note(window: NightWindow) -> None:
            nonlocal done
            score = round(window.score, 3) if window.score is not None else None
            facts_json = json.dumps(window.facts.note_input(score), ensure_ascii=False)
            async with limit:
                result = await write_note(
                    ctx.engine,
                    facts_json,
                    window_id=window.facts.window_id,
                    mode=note_mode(window.verdict),
                    terms=terms,
                )
            pending.extend(note_records(result))
            done += 1
            if time.monotonic() - flushed_at >= NOTE_FLUSH_S:
                flush()
            ctx.progress(done)

        try:
            async with asyncio.TaskGroup() as group:
                for window in windows:
                    group.create_task(note(window))
        finally:
            flush()

    async def report(self, ctx: RunContext) -> None:
        lines = write_report(ctx.db, ctx.run_id)
        logger.info(
            "night run %s report: %s",
            ctx.run_id,
            "; ".join(f"{line.line} {line.scanned} scanned" for line in lines),
        )
