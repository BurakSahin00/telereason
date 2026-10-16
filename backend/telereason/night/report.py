"""The morning report of a night run (#39), built from its stored windows and notes.

The 11 Oct morning report rules: on a line with a score (AURSAD,
`screening`) the windows scoring at least `SCORE_THRESHOLD` are listed by score, highest
first; on a `closed` line (FactoryWave) the windows with the largest |z| are listed. Ties keep
the night's order. Each line lists at most `ITEMS_PER_LINE` windows, and only a verified note
is shown: a window whose note failed the verifier gets an empty note and `verified: false`,
so the technician sees its facts instead.
"""

from collections.abc import Sequence

from telereason.api.schemas import GateLine, GateResponse, Line, ReportItem, ReportLine
from telereason.store.db import NightDb, StoredWindow

SCORE_THRESHOLD = 0.9
ITEMS_PER_LINE = 10
LINE_ORDER: tuple[Line, ...] = ("AURSAD", "FactoryWave")


def has_score(verdict: str) -> bool:
    """Only a `closed` line hides the gate score."""
    return verdict != "closed"


def flagged(score: float | None) -> bool | None:
    return None if score is None else score >= SCORE_THRESHOLD


def report_item(stored: StoredWindow, scored: bool) -> ReportItem:
    window = stored.window
    return ReportItem(
        window_id=window.window_id,
        task=window.task,
        score=window.score if scored else None,
        look_first=window.look_first,
        note=stored.note.text if stored.verified and stored.note else "",
        verified=stored.verified,
    )


def report_line(gate: GateLine, windows: Sequence[StoredWindow]) -> ReportLine:
    """One line of the report; `windows` are that line's windows in night order."""
    scored = has_score(gate.verdict)
    if scored:
        picked = [w for w in windows if w.window.score is not None]
        picked = [w for w in picked if w.window.score >= SCORE_THRESHOLD]
        n_flagged: int | None = len(picked)
        # sorted() is stable, so equal scores keep the night's order.
        picked.sort(key=lambda w: -(w.window.score or 0.0))
    else:
        n_flagged = None
        picked = sorted(
            (w for w in windows if w.window.max_abs_z is not None),
            key=lambda w: -abs(w.window.max_abs_z or 0.0),
        )
    return ReportLine(
        line=gate.line,
        verdict=gate.verdict,
        label=gate.label,
        scanned=len(windows),
        flagged=n_flagged,
        threshold=SCORE_THRESHOLD if scored else None,
        items=[report_item(w, scored) for w in picked[:ITEMS_PER_LINE]],
    )


def build_report(gate: GateResponse, windows: Sequence[StoredWindow]) -> list[ReportLine]:
    """Report lines in `LINE_ORDER` for every line the gate judged."""
    ordered = sorted(windows, key=lambda w: w.window.seq)
    by_line = {line: [w for w in ordered if w.window.line == line] for line in LINE_ORDER}
    gate_lines = {row.line: row for row in gate.lines}
    return [
        report_line(gate_lines[line], by_line[line]) for line in LINE_ORDER if line in gate_lines
    ]


def write_report(db: NightDb, run_id: str) -> list[ReportLine]:
    """Build the report of a run from the store and save it there."""
    run = db.get_run(run_id)
    if run is None or run.gate is None:
        raise ValueError(f"run {run_id} has no gate verdicts to report on")
    lines = build_report(run.gate, db.windows(run_id))
    db.put_report(run_id, lines)
    return lines
