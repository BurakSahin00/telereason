"""The morning report (#39): ranking rules, and the fixture night rebuilt in the store."""

import os
from pathlib import Path
from typing import Any

import pytest

from telereason.api.schemas import (
    Fact,
    FeatureContribution,
    GateLine,
    GateResponse,
    Line,
    ReportResponse,
    StepTimings,
    Verdict,
)
from telereason.night.report import (
    ITEMS_PER_LINE,
    SCORE_THRESHOLD,
    build_report,
    report_line,
    write_report,
)
from telereason.store.db import NightDb, NoteRecord, RunRecord, StoredWindow, WindowRecord
from telereason.store.night import DbNight
from tests.test_fixtures import load

IMPORTED_RUN = "2026-10-11-amd"
# Opt-in check against the real 11 Oct results: directories separated by os.pathsep.
RESULTS_ENV = "TELEREASON_11OCT_RESULTS"


def fixture_night(db: NightDb) -> None:
    """Store a night shaped like the fixture night: its windows plus filler windows.

    The fixture windows come first in night order, AURSAD by report rank and FactoryWave with
    decreasing deviations; the fillers bring the line counts and the AURSAD flagged count to the
    run's, and rank below every fixture window.
    """
    report = ReportResponse.model_validate(load("report_latest.json"))
    details = load("window_detail.json")
    lines = {line.line: line for line in report.lines}
    windows: list[WindowRecord] = []
    notes: list[NoteRecord] = []

    def add(window_id: str, line: Line, note: str, verified: bool, **fields: Any) -> None:
        windows.append(WindowRecord(window_id=window_id, line=line, seq=len(windows), **fields))
        notes.append(NoteRecord(window_id, 1, note, note, verified))

    fw_rank = 0
    for window_id, detail in details.items():
        fw_rank += detail["line"] == "FactoryWave"
        add(
            window_id,
            detail["line"],
            detail["note"],
            detail["verified"],
            task=detail["task"],
            score=detail["score"],
            flagged=detail["flagged"],
            max_abs_z=2000.0 - fw_rank if detail["line"] == "FactoryWave" else 1.0,
            look_first=detail["look_first"],
            facts=tuple(Fact.model_validate(fact) for fact in detail["facts"]),
        )
    free_ids = (f"W-{n:06d}" for n in range(10**6) if f"W-{n:06d}" not in details)
    aursad = lines["AURSAD"]
    n_aursad = sum(1 for w in windows if w.line == "AURSAD")
    n_flagged = sum(1 for w in windows if w.flagged)
    for n in range(aursad.scanned - n_aursad):
        score = 0.95 if n < (aursad.flagged or 0) - n_flagged else 0.5
        flags = score >= SCORE_THRESHOLD
        add(next(free_ids), "AURSAD", "Score.", True, task="aursad", score=score, flagged=flags)
    n_fw = sum(1 for w in windows if w.line == "FactoryWave")
    for _ in range(lines["FactoryWave"].scanned - n_fw):
        add(next(free_ids), "FactoryWave", "Facts.", True, task="screwing", max_abs_z=1.0)

    t = report.timings_s
    db.init().replace_run(
        RunRecord(
            run_id=report.run_id,
            status="done",
            source="import",
            step="report",
            done=len(windows),
            total=len(windows),
            finished_at="2026-10-11",
            step_timings=StepTimings(gate=t.gate, facts=t.facts, scan=t.scan, report=t.report),
            total_s=t.total,
            gate=GateResponse.model_validate(load("gate.json")),
        ),
        windows,
        notes,
    )
    write_report(db, report.run_id)


def gate_line(line: Line, verdict: Verdict) -> GateLine:
    return GateLine(
        line=line, verdict=verdict, label=verdict, auroc=0.9, auroc_ci=(0.8, 1.0), explanation=""
    )


def stored(
    window_id: str,
    seq: int,
    *,
    line: Line = "AURSAD",
    score: float | None = None,
    max_abs_z: float | None = None,
    verified: bool = True,
) -> StoredWindow:
    window = WindowRecord(
        window_id=window_id,
        line=line,
        task="aursad",
        seq=seq,
        score=score,
        flagged=None if score is None else score >= SCORE_THRESHOLD,
        max_abs_z=max_abs_z,
        top_features=(FeatureContribution(feature="x.mean", contribution=0.4),),
    )
    note = NoteRecord(window_id, 1, f"**{window_id}**", window_id, verified)
    return StoredWindow(window, note)


def test_report_of_the_fixture_night_is_the_stored_report(tmp_path: Path) -> None:
    db = NightDb(tmp_path / "night.sqlite")

    fixture_night(db)

    assert DbNight(db).report().model_dump(mode="json") == load("report_latest.json")


def test_scored_line_lists_flagged_windows_by_score_keeping_night_order() -> None:
    windows = [
        stored("W-000001", 0, score=0.95),
        stored("W-000002", 1, score=0.99),
        stored("W-000003", 2, score=0.5),
        stored("W-000004", 3, score=0.99),
        stored("W-000005", 4, score=SCORE_THRESHOLD),
    ]

    line = report_line(gate_line("AURSAD", "screening"), windows)

    assert [i.window_id for i in line.items] == ["W-000002", "W-000004", "W-000001", "W-000005"]
    assert (line.scanned, line.flagged, line.threshold) == (5, 4, SCORE_THRESHOLD)


def test_closed_line_lists_largest_deviations_without_scores() -> None:
    windows = [
        stored("W-000001", 0, line="FactoryWave", score=0.99, max_abs_z=2.0),
        stored("W-000002", 1, line="FactoryWave", max_abs_z=-9.0),
        stored("W-000003", 2, line="FactoryWave", max_abs_z=None),
        stored("W-000004", 3, line="FactoryWave", max_abs_z=5.0),
    ]

    line = report_line(gate_line("FactoryWave", "closed"), windows)

    assert [i.window_id for i in line.items] == ["W-000002", "W-000004", "W-000001"]
    assert (line.flagged, line.threshold) == (None, None)
    assert all(item.score is None for item in line.items)


def test_only_verified_notes_are_shown() -> None:
    windows = [stored("W-000001", 0, score=1.0, verified=False), stored("W-000002", 1, score=1.0)]

    items = report_line(gate_line("AURSAD", "screening"), windows).items

    assert [(i.note, i.verified) for i in items] == [("", False), ("W-000002", True)]


def test_each_line_lists_at_most_ten_windows() -> None:
    windows = [stored(f"W-{n:06d}", n, score=1.0) for n in range(ITEMS_PER_LINE + 5)]

    line = report_line(gate_line("AURSAD", "screening"), windows)

    assert len(line.items) == ITEMS_PER_LINE
    assert line.flagged == ITEMS_PER_LINE + 5


def test_lines_come_in_report_order_for_judged_lines_only() -> None:
    gate = GateResponse(
        run_id="run-1",
        lines=[gate_line("FactoryWave", "closed"), gate_line("AURSAD", "screening")],
    )
    windows = [stored("W-000001", 1, line="FactoryWave", max_abs_z=1.0), stored("W-000002", 0)]

    lines = build_report(gate, windows)
    only_fw = build_report(GateResponse(run_id="run-1", lines=gate.lines[:1]), windows)

    assert [line.line for line in lines] == ["AURSAD", "FactoryWave"]
    assert [line.scanned for line in lines] == [1, 1]
    assert [line.line for line in only_fw] == ["FactoryWave"]


@pytest.mark.skipif(
    not os.environ.get(RESULTS_ENV) or not os.environ.get("DATA_DIR"),
    reason=f"needs the 11 Oct results ({RESULTS_ENV}) and the dataset (DATA_DIR)",
)
def test_imported_11_oct_report_is_the_stored_report(tmp_path: Path) -> None:
    from scripts.import_night import import_night

    db = NightDb(tmp_path / "night.sqlite")
    results = [Path(p) for p in os.environ[RESULTS_ENV].split(os.pathsep)]

    import_night(results, os.environ["DATA_DIR"], db)
    report = DbNight(db).report()

    # The fixtures come from a later night, so the 11 Oct run is checked against its own counts.
    assert report.run_id == IMPORTED_RUN
    assert [(line.line, line.verdict, line.scanned, line.flagged) for line in report.lines] == [
        ("AURSAD", "screening", 4068, 1879),
        ("FactoryWave", "closed", 3354, None),
    ]
