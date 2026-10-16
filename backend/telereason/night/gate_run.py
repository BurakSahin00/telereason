"""The gate step of a night run: run the gate in its own process, or reuse a stored gate.

A full night runs ``python -m telereason.night.gate --out <run dir>`` as a child process with the
same Python, the same DATA_DIR and the environment of the app (so GATE_DEVICE reaches it); the
event loop is not blocked, and a cancelled run stops the child. The run directory is
``runs/<run_id>/`` next to DB_PATH. The night reads only ``gate.json`` and ``scores.jsonl``.

A small night (fewer windows than the gate tests) does not train the gate again: it reuses the
verdicts and scores of the latest finished run that has a gate of its own, either a full live
run (its ``gate.json`` and ``scores.jsonl`` are still in its run directory) or the imported
11 Oct night (its scores are in the store). Without one, or with GATE_MODE=full, the gate runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import telereason
from telereason.api.schemas import FeatureContribution, GateLine, GateResponse, Line, Verdict
from telereason.night.gate import GATE_FILE, SCORES_FILE
from telereason.store.db import NightDb

logger = logging.getLogger(__name__)

GATE_LOG = "gate.log"
LOG_TAIL_LINES = 20
# The API texts per verdict; `closed` and `screening` are the ones in docs/api.md.
GATE_TEXTS: dict[Verdict, tuple[str, str]] = {
    "closed": (
        "Facts, not diagnosis",
        "On new days, fault prediction is no better than chance, so this line gets facts only.",
    ),
    "screening": (
        "Abnormal-behaviour screening only",
        "Normal and faulty runs separate well, but the fault cause is not reliable.",
    ),
    "open": (
        "Diagnosis allowed",
        "Every gate check passes on this line, so a fault diagnosis may be shown.",
    ),
}
LINE_ORDER: tuple[Line, ...] = ("FactoryWave", "AURSAD")


class GateFailedError(RuntimeError):
    """The gate process stopped with an error or left no output."""


@dataclass(frozen=True)
class GateWindow:
    """What the gate says about one window it tested."""

    # 1 - P(normal), out of fold; shown only on a line with a score.
    score: float | None = None
    fold: int | None = None
    # Gate feature contributions (#45); empty until they are on main.
    top_features: tuple[FeatureContribution, ...] = ()


@dataclass(frozen=True)
class GateOutput:
    verdicts: GateResponse
    # Every window the gate tested, by window id: the windows a night can scan.
    windows: Mapping[str, GateWindow]
    # The gate's class labels; empty when the source does not list them.
    classes: tuple[str, ...] = ()
    # Run whose gate this is (the run itself for a full night).
    source_run: str = ""


# Writes gate.json and scores.jsonl into the given directory.
GateProcess = Callable[[Path, str], Awaitable[None]]


def run_dir(db: NightDb, run_id: str) -> Path:
    return db.path.parent / "runs" / run_id


def gate_command(out: Path, data_dir: str) -> tuple[str, ...]:
    """The gate's command line: the app's own Python."""
    return (
        sys.executable,
        "-m",
        "telereason.night.gate",
        "--out",
        str(out),
        "--data-dir",
        data_dir,
    )


async def run_child(command: Sequence[str], out: Path, env: Mapping[str, str]) -> None:
    """Run `command` without blocking the event loop; its output goes to `out/gate.log`.

    A cancelled run kills the child before the cancellation goes on. A non-zero exit raises
    `GateFailedError` with the end of the log."""
    out.mkdir(parents=True, exist_ok=True)
    package_root = Path(telereason.__file__).resolve().parents[1]
    with open(out / GATE_LOG, "wb") as log_file:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=package_root,
            env=dict(env),
            stdout=log_file,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            code = await process.wait()
        except asyncio.CancelledError:
            if process.returncode is None:
                process.kill()
                await process.wait()
            raise
    if code != 0:
        tail = (out / GATE_LOG).read_text(encoding="utf-8", errors="replace").splitlines()
        message = "\n".join(tail[-LOG_TAIL_LINES:])
        raise GateFailedError(f"the gate process exited with code {code}: {message}")


async def gate_process(out: Path, data_dir: str) -> None:
    """Run the gate in its own process with the app's environment and the same DATA_DIR."""
    await run_child(gate_command(out, data_dir), out, {**os.environ, "DATA_DIR": data_dir})


def gate_line(line: Line, result: dict[str, Any]) -> GateLine | None:
    """One line of `GateResponse` from `gate.json`; None when the gate measured no AUROC."""
    verdict: Verdict = result["verdict"]
    anomaly = result.get("anomaly") or {}
    auroc = anomaly.get("auroc")
    low, high = (list(anomaly.get("interval") or []) + [None, None])[:2]
    if auroc is None or low is None or high is None:
        logger.warning("gate: %s has no measured AUROC (%s); left out", line, result["decision"])
        return None
    label, explanation = GATE_TEXTS[verdict]
    return GateLine(
        line=line,
        verdict=verdict,
        label=label,
        auroc=auroc,
        auroc_ci=(low, high),
        explanation=explanation,
    )


def read_gate_files(out: Path, run_id: str) -> GateOutput:
    """`gate.json` and `scores.jsonl` in `out` as the night's gate output."""
    gate_path, scores_path = out / GATE_FILE, out / SCORES_FILE
    if not gate_path.is_file() or not scores_path.is_file():
        raise GateFailedError(f"no {GATE_FILE} and {SCORES_FILE} in {out}")
    document = json.loads(gate_path.read_text(encoding="utf-8"))
    lines = document.get("lines") or {}
    rows = [gate_line(line, lines[line]) for line in LINE_ORDER if line in lines]
    windows: dict[str, GateWindow] = {}
    with open(scores_path, encoding="utf-8") as handle:
        for raw in handle:
            if raw.strip():
                record = json.loads(raw)
                windows[record["window_id"]] = GateWindow(
                    score=record.get("score"), fold=record.get("fold")
                )
    return GateOutput(
        verdicts=GateResponse(run_id=run_id, lines=[row for row in rows if row]),
        windows=windows,
        classes=tuple(document.get("classes") or ()),
        source_run=run_id,
    )


def stored_gate(db: NightDb, run_id: str) -> GateOutput | None:
    """The gate of a finished run from the store: verdicts and the scores of its windows."""
    run = db.get_run(run_id)
    if run is None or run.gate is None:
        return None
    windows = {
        stored.window.window_id: GateWindow(score=stored.window.score, fold=stored.window.fold)
        for stored in db.windows(run_id)
    }
    return GateOutput(verdicts=run.gate, windows=windows, source_run=run_id)


def reusable_gate(db: NightDb) -> GateOutput | None:
    """The gate of the latest finished run that has one of its own (see the module docstring)."""
    for run in db.finished_runs():
        out = run_dir(db, run.run_id)
        if (out / GATE_FILE).is_file() and (out / SCORES_FILE).is_file():
            return read_gate_files(out, run.run_id)
        if run.source == "import":
            return stored_gate(db, run.run_id)
    return None
