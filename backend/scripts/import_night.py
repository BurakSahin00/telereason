"""Load the 11 Oct MI300X night into the SQLite store as its first run, "2026-10-11-amd".

Run from backend/:
    uv run python -m scripts.import_night --results <results dir> [--results <more>] \
        [--data-dir <DATA_DIR>] [--db <DB_PATH>]

Reads `p35_gece.json` (timings, counts), `kapi_gpu.json` (gate verdicts) and
`gece_notlar.jsonl` (one row per window: facts, score, notes) from the first results directory
that has each file. `--data-dir` and `--db` default to DATA_DIR and DB_PATH.

The notes do not carry the FactoryBench item id, so each note is looked up in the night pool
(`telereason.night.windows.load_pool`, the 8,508 L4-t1 windows): a note's window id is the
plain 11 Oct id of its item (`base_window_id`). Where more than one pool window of the note's
line has that plain id (28 ids occur twice in the run), the note goes to the window whose channel
means round to the note's `window_mean`. The store keeps the pool's window id, item id and group,
so every stored window opens in `/api/windows/{window_id}/signals`.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from telereason.ai.markdown import clean_note
from telereason.api.schemas import (
    Fact,
    GateLine,
    GateResponse,
    Line,
    ReportLine,
    StepTimings,
    Verdict,
)
from telereason.data.paths import dataset_dir
from telereason.night.facts import plain_reading
from telereason.night.gate_run import GATE_TEXTS, LINE_ORDER
from telereason.night.report import SCORE_THRESHOLD, build_report, flagged
from telereason.night.windows import POOL_FILE, Pool, Window, base_window_id, load_pool
from telereason.settings import load_settings
from telereason.store.db import NightDb, NoteRecord, RunRecord, StoredWindow, WindowRecord

logger = logging.getLogger("import_night")

RUN_ID = "2026-10-11-amd"
RUN_DATE = "2026-10-11"
# Gate verdicts as the results spell them.
VERDICTS: dict[str, Verdict] = {"DAGITMA": "closed", "KOSULLU": "screening", "GECER": "open"}
NIGHT_FILE = "p35_gece.json"
GATE_FILE = "kapi_gpu.json"
NOTES_FILE = "gece_notlar.jsonl"


class NightImportError(RuntimeError):
    """The results or the dataset do not describe one consistent night."""


# ---------------------------------------------------------------- results


@dataclass(frozen=True)
class NightNote:
    """One row of gece_notlar.jsonl."""

    seq: int
    window_id: str
    line: Line
    score: float | None
    max_abs_z: float | None
    facts: dict[str, Any]
    attempts: list[dict[str, Any]]
    verified: bool


def find(results: Sequence[Path], name: str) -> Path:
    for folder in results:
        if (folder / name).is_file():
            return folder / name
    raise NightImportError(f"{name} is in none of {', '.join(str(folder) for folder in results)}")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def read_notes(path: Path) -> list[NightNote]:
    notes = []
    with path.open(encoding="utf-8") as handle:
        for seq, raw in enumerate(handle):
            if not raw.strip():
                continue
            row = json.loads(raw)
            notes.append(
                NightNote(
                    seq=seq,
                    window_id=row["pencere"],
                    line=row["hat"],
                    score=row["skor"],
                    max_abs_z=row["en_z"],
                    facts=json.loads(row["girdi"]),
                    attempts=row["deneme"],
                    verified=bool(row["son_gecti"]),
                )
            )
    return notes


def gate_response(gate_run: dict[str, Any]) -> GateResponse:
    lines = []
    for line in LINE_ORDER:
        verdict = VERDICTS[gate_run["hat"][line]["karar"]]
        anomaly = gate_run["hat"][line]["anomali"]
        low, high = anomaly["aralik"]
        label, explanation = GATE_TEXTS[verdict]
        lines.append(
            GateLine(
                line=line,
                verdict=verdict,
                label=label,
                auroc=anomaly["auroc"],
                auroc_ci=(low, high),
                explanation=explanation,
            )
        )
    return GateResponse(run_id=RUN_ID, lines=lines)


# ---------------------------------------------------------------- pool


def y3(value: float) -> float:
    """Three significant digits, values below 0.001 as 0: the rounding of the 11 Oct facts."""
    return 0.0 if abs(value) < 1e-3 else float(f"{value:.3g}")


def fact_matches(window: Window, note: NightNote) -> int:
    """How many of the note's deviating signals have the window's rounded channel mean."""
    matches = 0
    for signal in note.facts.get("deviating_signals") or []:
        column = window.column(signal["signal"])
        if column is None or not np.isfinite(column).any():
            continue
        if y3(float(np.nanmean(column))) == signal["window_mean"]:
            matches += 1
    return matches


def resolve_windows(notes: Sequence[NightNote], pool: Pool) -> tuple[list[Window], Counter[str]]:
    """The pool window of every note, in note order, and counts for the log."""
    by_plain_id: dict[tuple[str, Line], list[Window]] = defaultdict(list)
    for window in pool.windows:
        by_plain_id[base_window_id(window.ms_id), window.line].append(window)

    counts: Counter[str] = Counter()
    resolved = []
    for note in notes:
        options = by_plain_id[note.window_id, note.line]
        if not options:
            raise NightImportError(f"{note.window_id} ({note.line}): not in the night pool")
        if len(options) > 1:
            counts["matched_by_facts"] += 1
            scored = sorted(((fact_matches(w, note), w) for w in options), key=lambda x: -x[0])
            (best, window), (runner_up, _) = scored[0], scored[1]
            if best == runner_up:
                raise NightImportError(f"{note.window_id}: two pool windows fit the note's facts")
            if best < len(note.facts.get("deviating_signals") or []):
                counts["partial_fact_match"] += 1
        else:
            window = options[0]
        resolved.append(window)
    if len({w.window_id for w in resolved}) != len(resolved):
        raise NightImportError("two notes resolved to the same pool window")
    return resolved, counts


# ---------------------------------------------------------------- run


def window_record(note: NightNote, window: Window) -> WindowRecord:
    facts = tuple(
        Fact(
            signal=signal["signal"],
            window_mean=signal["window_mean"],
            normal_mean=signal["normal_mean"],
            difference=signal["difference"],
            z=signal["z"],
            plain=plain_reading(signal["z"]),
        )
        for signal in note.facts.get("deviating_signals") or []
    )
    return WindowRecord(
        window_id=window.window_id,
        line=note.line,
        task=str(note.facts.get("task") or "unknown"),
        seq=note.seq,
        ms_id=window.ms_id,
        group_id=window.group,
        score=note.score,
        flagged=flagged(note.score),
        max_abs_z=note.max_abs_z,
        look_first=str(note.facts.get("look_first") or ""),
        facts=facts,
    )


def note_records(note: NightNote, window_id: str) -> list[NoteRecord]:
    records = []
    for number, attempt in enumerate(note.attempts, start=1):
        violations = [str(v) for v in attempt.get("ihlal") or []]
        violations += [f"unsourced number {v}" for v in attempt.get("kaynaksiz") or []]
        seconds = attempt.get("s")
        records.append(
            NoteRecord(
                window_id=window_id,
                attempt=number,
                text_raw=attempt["metin"],
                text=clean_note(attempt["metin"]),
                passed=bool(attempt.get("gecti")),
                violations=tuple(violations),
                completion_tokens=attempt.get("token"),
                ms=round(seconds * 1000) if seconds is not None else None,
            )
        )
    return records


@dataclass(frozen=True)
class ImportedNight:
    run: RunRecord
    windows: list[WindowRecord]
    notes: list[NoteRecord]
    report: list[ReportLine]
    counts: dict[str, int]


def build(results: Sequence[Path], data_dir: str | Path) -> ImportedNight:
    night = read_json(find(results, NIGHT_FILE))
    gate = gate_response(read_json(find(results, GATE_FILE)))
    rows = read_notes(find(results, NOTES_FILE))
    if len(rows) != night["N"]:
        raise NightImportError(f"{NOTES_FILE} has {len(rows)} rows, {NIGHT_FILE} N={night['N']}")

    pool_path = dataset_dir(data_dir) / POOL_FILE
    if not pool_path.is_file():
        raise NightImportError(f"{pool_path} is missing; download the dataset first")
    pool = load_pool(data_dir)
    resolved, counts = resolve_windows(rows, pool)

    windows = [window_record(r, w) for r, w in zip(rows, resolved, strict=True)]
    notes = [n for r, w in zip(rows, windows, strict=True) for n in note_records(r, w.window_id)]

    n_flagged = sum(bool(w.flagged) for w in windows if w.line == "AURSAD")
    if n_flagged != night["rapor"]["AURSAD"]["isaretli"]:
        raise NightImportError(f"threshold {SCORE_THRESHOLD} flags {n_flagged} AURSAD windows")
    last_notes = {n.window_id: n for n in notes}  # attempts are in order, so the last one wins
    report = build_report(gate, [StoredWindow(w, last_notes.get(w.window_id)) for w in windows])

    sure = night["sure_s"]
    run = RunRecord(
        run_id=RUN_ID,
        status="done",
        source="import",
        step="report",
        done=len(windows),
        total=len(windows),
        finished_at=RUN_DATE,
        step_timings=StepTimings(
            gate=sure["kapi"], facts=sure["girdi"], scan=sure["tarama"], report=sure["rapor"]
        ),
        total_s=sure["toplam"],
        gate=gate,
    )
    repeated = Counter(row.window_id for row in rows)
    counts.update(
        notes=len(rows),
        windows=len(windows),
        attempts=len(notes),
        repeated_ids=sum(1 for n in repeated.values() if n > 1),
        pool=len(pool.windows),
        renamed=sum(1 for w in windows if w.window_id != base_window_id(w.ms_id or "")),
        flagged=n_flagged,
    )
    return ImportedNight(run=run, windows=windows, notes=notes, report=report, counts=dict(counts))


def import_night(results: Sequence[Path], data_dir: str | Path, db: NightDb) -> ImportedNight:
    imported = build(results, data_dir)
    db.init().replace_run(imported.run, imported.windows, imported.notes, imported.report)
    return imported


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--data-dir", help="default: DATA_DIR")
    parser.add_argument("--db", help="default: DB_PATH")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Settings are read only for what the command line leaves out.
    if args.data_dir is None or args.db is None:
        settings = load_settings()
        args.data_dir = args.data_dir or settings.data_dir
        args.db = args.db or settings.db_path
    try:
        imported = import_night(args.results, args.data_dir, NightDb(args.db))
    except NightImportError as exc:
        raise SystemExit(str(exc)) from exc
    counts = imported.counts
    logger.info(
        "run %s: %d notes, %d windows from a pool of %d, %d note attempts; %d repeated ids, "
        "%d notes matched to their window by facts, %d ids differ from the plain 11 Oct id; "
        "%d AURSAD windows flagged; written to %s",
        imported.run.run_id,
        counts["notes"],
        counts["windows"],
        counts["pool"],
        counts["attempts"],
        counts["repeated_ids"],
        counts.get("matched_by_facts", 0),
        counts["renamed"],
        counts["flagged"],
        args.db,
    )
    if counts.get("partial_fact_match"):
        logger.warning("%d notes matched only some of their facts", counts["partial_fact_match"])


if __name__ == "__main__":
    main()
