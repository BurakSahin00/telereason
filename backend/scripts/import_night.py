"""Load the 11 Oct MI300X night into the SQLite store as its first run, "2026-10-11-amd".

Run from backend/:
    uv run python -m scripts.import_night --results <results dir> [--results <more>] \
        [--data-dir <DATA_DIR>] [--db <DB_PATH>]

Reads `p35_gece.json` (timings, counts), `kapi_gpu.json` (gate verdicts) and
`gece_notlar.jsonl` (one row per window: facts, score, notes) from the first results directory
that has each file. `--data-dir` and `--db` default to DATA_DIR and DB_PATH.

The notes do not carry the FactoryBench item id, so the item is looked up in the L4-t1 items of
the downloaded dataset by its window id. 28 window ids occur twice in the run; such a note is
matched to its item by its line and facts (the item whose channel means round to the note's
`window_mean`). Ids are then made unique with the #29 rule: in a collision the item id that
sorts first keeps the plain id, every other item is re-hashed with a `#n` suffix.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import zlib
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scripts.make_fixtures import (
    EXPLANATIONS,
    LABELS,
    LINE_ORDER,
    REPORT_THRESHOLD,
    RUN_ID,
    VERDICTS,
    clean_note,
    plain_reading,
)
from telereason.api.schemas import Fact, GateLine, GateResponse, Line, StepTimings
from telereason.data.paths import dataset_dir
from telereason.settings import load_settings
from telereason.store.db import NightDb, NoteRecord, RunRecord, WindowRecord

logger = logging.getLogger("import_night")

RUN_DATE = "2026-10-11"
NIGHT_FILE = "p35_gece.json"
GATE_FILE = "kapi_gpu.json"
NOTES_FILE = "gece_notlar.jsonl"
ITEMS_FILE = "factorybench_qa/level_4.jsonl"
TEMPLATE_ID = 1


class NightImportError(RuntimeError):
    """The results or the dataset do not describe one consistent night."""


# ---------------------------------------------------------------- window ids


def base_window_id(ms_id: str) -> str:
    """`W-` plus the last six decimal digits of the CRC-32 of the item id, as on 11 Oct."""
    return f"W-{zlib.crc32(ms_id.encode()) % 10**6:06d}"


def assign_window_ids(ms_ids: Iterable[str]) -> dict[str, str]:
    """Item id -> window id, unique over the given items.

    The same rule as `night/windows.py` in the facts PR (#29); this helper goes once that is
    on main.
    """
    by_base: dict[str, list[str]] = {}
    for ms_id in sorted(set(ms_ids)):
        by_base.setdefault(base_window_id(ms_id), []).append(ms_id)
    taken = set(by_base)
    out: dict[str, str] = {}
    for base, members in sorted(by_base.items()):
        out[members[0]] = base
        for ms_id in members[1:]:
            n = 1
            while (candidate := base_window_id(f"{ms_id}#{n}")) in taken:
                n += 1
            taken.add(candidate)
            out[ms_id] = candidate
    return out


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
        lines.append(
            GateLine(
                line=line,
                verdict=verdict,
                label=LABELS[verdict],
                auroc=anomaly["auroc"],
                auroc_ci=(low, high),
                explanation=EXPLANATIONS[verdict],
            )
        )
    return GateResponse(run_id=RUN_ID, lines=lines)


# ---------------------------------------------------------------- items


def y3(value: float) -> float:
    """Three significant digits, values below 0.001 as 0: the rounding of the 11 Oct facts."""
    return 0.0 if abs(value) < 1e-3 else float(f"{value:.3g}")


def channel_mean(item: dict[str, Any], channel: str) -> float | None:
    """Mean of one channel over the item's `t=<ms>: acronym=value, ...` rows."""
    context = item.get("context") or {}
    fmt = context.get("time_series_format") or {}
    acronyms = fmt.get("acronym_mapping") or context.get("acronym_mapping") or {}
    key = next((short for short, long in acronyms.items() if long == channel), channel)
    rows = context.get("time_series") or []
    if isinstance(rows, str):
        rows = rows.splitlines()
    values = []
    for row in rows:
        _, colon, body = str(row).partition(":")
        if not colon:
            continue
        for part in body.split(","):
            name, equals, text = part.partition("=")
            if equals and name.strip() == key:
                try:
                    value = float(text)
                except ValueError:
                    continue
                if math.isfinite(value):
                    values.append(value)
    return sum(values) / len(values) if values else None


def fact_matches(item: dict[str, Any], note: NightNote) -> int:
    """How many of the note's deviating signals have the item's rounded channel mean."""
    matches = 0
    for signal in note.facts.get("deviating_signals") or []:
        mean = channel_mean(item, signal["signal"])
        if mean is not None and y3(mean) == signal["window_mean"]:
            matches += 1
    return matches


def item_line(item: dict[str, Any]) -> Line:
    dataset = str((item.get("provenance") or {}).get("dataset") or "").lower()
    return "AURSAD" if dataset == "aursad" else "FactoryWave"


def iter_items(path: Path) -> Iterator[dict[str, Any]]:
    """L4-t1 items with an id, voraus-AD (CC BY-NC-SA 4.0) left out."""
    with path.open(encoding="utf-8") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            item = json.loads(raw)
            if item.get("template_id") != TEMPLATE_ID or not item.get("id"):
                continue
            if "voraus" in json.dumps(item.get("provenance") or {}).lower():
                continue
            yield item


def resolve_items(notes: Sequence[NightNote], items_path: Path) -> tuple[list[str], Counter[str]]:
    """The item id of every note, in note order, and counts for the log.

    A first pass collects the items behind each window id; a second pass compares facts only
    where a note has more than one item of its line to choose from.
    """
    notes_by_id: dict[str, list[NightNote]] = defaultdict(list)
    for note in notes:
        notes_by_id[note.window_id].append(note)
    found: dict[tuple[str, Line], list[str]] = defaultdict(list)
    for item in iter_items(items_path):
        ms_id = str(item["id"])
        window_id = base_window_id(ms_id)
        if window_id in notes_by_id:
            found[window_id, item_line(item)].append(ms_id)

    ambiguous = {note.seq: note for note in notes if len(found[note.window_id, note.line]) > 1}
    matches: dict[tuple[int, str], int] = {}
    if ambiguous:
        wanted = {ms_id for n in ambiguous.values() for ms_id in found[n.window_id, n.line]}
        for item in iter_items(items_path):
            ms_id = str(item["id"])
            if ms_id not in wanted:
                continue
            for note in notes_by_id[base_window_id(ms_id)]:
                if note.seq in ambiguous and ms_id in found[note.window_id, note.line]:
                    matches[note.seq, ms_id] = fact_matches(item, note)

    counts: Counter[str] = Counter()
    ms_ids = []
    for note in notes:
        options = found[note.window_id, note.line]
        if not options:
            raise NightImportError(f"{note.window_id} ({note.line}): no L4-t1 item in {items_path}")
        if note.seq in ambiguous:
            counts["matched_by_facts"] += 1
            ranked = sorted(options, key=lambda ms_id: -matches.get((note.seq, ms_id), 0))
            best, runner_up = (matches.get((note.seq, ms_id), 0) for ms_id in ranked[:2])
            if best == runner_up:
                raise NightImportError(f"{note.window_id}: two items fit the note's facts equally")
            if best < len(note.facts.get("deviating_signals") or []):
                counts["partial_fact_match"] += 1
            ms_ids.append(ranked[0])
        else:
            ms_ids.append(options[0])
    if len(set(ms_ids)) != len(ms_ids):
        raise NightImportError("two notes resolved to the same item")
    return ms_ids, counts


# ---------------------------------------------------------------- run


def window_record(note: NightNote, window_id: str, ms_id: str) -> WindowRecord:
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
        window_id=window_id,
        line=note.line,
        task=str(note.facts.get("task") or "unknown"),
        seq=note.seq,
        ms_id=ms_id,
        score=note.score,
        flagged=None if note.score is None else note.score >= REPORT_THRESHOLD,
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
    counts: dict[str, int]


def build(results: Sequence[Path], data_dir: str | Path) -> ImportedNight:
    night = read_json(find(results, NIGHT_FILE))
    gate = gate_response(read_json(find(results, GATE_FILE)))
    rows = read_notes(find(results, NOTES_FILE))
    if len(rows) != night["N"]:
        raise NightImportError(f"{NOTES_FILE} has {len(rows)} rows, {NIGHT_FILE} N={night['N']}")

    items_path = dataset_dir(data_dir) / ITEMS_FILE
    if not items_path.is_file():
        raise NightImportError(f"{items_path} is missing; download the dataset first")
    ms_ids, counts = resolve_items(rows, items_path)
    window_ids = assign_window_ids(ms_ids)

    windows = [window_record(r, window_ids[m], m) for r, m in zip(rows, ms_ids, strict=True)]
    notes = [n for r, w in zip(rows, windows, strict=True) for n in note_records(r, w.window_id)]

    flagged = sum(bool(w.flagged) for w in windows if w.line == "AURSAD")
    if flagged != night["rapor"]["AURSAD"]["isaretli"]:
        raise NightImportError(f"threshold {REPORT_THRESHOLD} flags {flagged} AURSAD windows")

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
        renamed=sum(1 for w in windows if w.window_id != base_window_id(w.ms_id or "")),
        flagged=flagged,
    )
    return ImportedNight(run=run, windows=windows, notes=notes, counts=dict(counts))


def import_night(results: Sequence[Path], data_dir: str | Path, db: NightDb) -> ImportedNight:
    imported = build(results, data_dir)
    db.init().replace_run(imported.run, imported.windows, imported.notes)
    return imported


def main() -> None:
    settings = load_settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, action="append", required=True)
    parser.add_argument("--data-dir", default=settings.data_dir)
    parser.add_argument("--db", default=settings.db_path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        imported = import_night(args.results, args.data_dir, NightDb(args.db))
    except NightImportError as exc:
        raise SystemExit(str(exc)) from exc
    counts = imported.counts
    logger.info(
        "run %s: %d notes, %d windows, %d note attempts; %d repeated ids, %d notes matched "
        "to their item by facts, %d ids re-hashed; %d AURSAD windows flagged; written to %s",
        imported.run.run_id,
        counts["notes"],
        counts["windows"],
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
