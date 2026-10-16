"""SQLite store of night runs (#48): runs, their windows, notes and morning reports.

The tables are in `schema.sql`. The database runs in WAL mode, so the API keeps reading
while a night run writes. Every call opens its own short connection: FastAPI reads from
worker threads and the night runner writes from the event loop, and a connection per call
needs no locking of our own.

JSON columns hold API models from `api/schemas.py`, so the endpoints serve what was stored
without changing a schema.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import TypeAdapter

from telereason.api.schemas import (
    Fact,
    FeatureContribution,
    GateResponse,
    Line,
    NightStep,
    ReportItem,
    ReportLine,
    RunStatus,
    StepTimings,
    Timings,
)

SCHEMA = Path(__file__).with_name("schema.sql")
BUSY_TIMEOUT_S = 10.0
ACTIVE_STATUSES: tuple[RunStatus, ...] = ("queued", "running")
STEP_COLUMNS: dict[NightStep, str] = {
    "gate": "t_gate",
    "facts": "t_facts",
    "scan": "t_scan",
    "report": "t_report",
}
# Columns `update_run` may change; `gate` and `timings` are written through their own keywords.
RUN_COLUMNS = frozenset(
    {"status", "step", "done", "total", "started_at", "finished_at", "t_total", "error"}
    | set(STEP_COLUMNS.values())
)
WindowSort = Literal["score", "deviation"]
RunSource = Literal["import", "live"]

_FACTS = TypeAdapter(list[Fact])
_FEATURES = TypeAdapter(list[FeatureContribution])
_ITEMS = TypeAdapter(list[ReportItem])

_SORTS: dict[WindowSort | None, str] = {
    None: "w.seq",
    "score": "w.score IS NULL, w.score DESC, w.seq",
    "deviation": "w.max_abs_z IS NULL, w.max_abs_z DESC, w.seq",
}


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    status: RunStatus
    source: RunSource
    step: NightStep | None = None
    done: int = 0
    total: int = 0
    max_windows: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    step_timings: StepTimings = field(default_factory=StepTimings)
    total_s: float | None = None
    gate: GateResponse | None = None
    error: str | None = None

    @property
    def timings(self) -> Timings | None:
        """All five timings once every step has finished, else None."""
        steps = self.step_timings
        values = (steps.gate, steps.facts, steps.scan, steps.report, self.total_s)
        if any(value is None for value in values):
            return None
        gate, facts, scan, report, total = (float(value or 0) for value in values)
        return Timings(gate=gate, facts=facts, scan=scan, report=report, total=total)


@dataclass(frozen=True)
class WindowRecord:
    window_id: str
    line: Line
    task: str
    seq: int
    ms_id: str | None = None
    group_id: str | None = None
    fold: int | None = None
    score: float | None = None
    flagged: bool | None = None
    max_abs_z: float | None = None
    look_first: str = ""
    facts: tuple[Fact, ...] = ()
    top_features: tuple[FeatureContribution, ...] = ()


@dataclass(frozen=True)
class NoteRecord:
    window_id: str
    attempt: int
    text_raw: str
    text: str
    passed: bool
    violations: tuple[str, ...] = ()
    completion_tokens: int | None = None
    ms: int | None = None


@dataclass(frozen=True)
class StoredWindow:
    """A window with the last attempt of its note (None before the scan wrote one)."""

    window: WindowRecord
    note: NoteRecord | None

    @property
    def verified(self) -> bool:
        return self.note is not None and self.note.passed


class NightDb:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """One connection and one transaction: committed on success, rolled back on error."""
        conn = sqlite3.connect(self.path, timeout=BUSY_TIMEOUT_S)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys = ON")
            with conn:
                yield conn
        finally:
            conn.close()

    def init(self) -> NightDb:
        """Create the file and the tables if missing, and switch the file to WAL."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(SCHEMA.read_text(encoding="utf-8"))
        return self

    # ------------------------------------------------------------ runs

    def create_run(self, run: RunRecord) -> None:
        with self.connect() as conn:
            _insert_run(conn, run)

    def update_run(
        self,
        run_id: str,
        *,
        gate: GateResponse | None = None,
        timings: StepTimings | None = None,
        **columns: Any,
    ) -> None:
        unknown = set(columns) - RUN_COLUMNS
        if unknown:
            raise ValueError(f"night_runs has no updatable column {sorted(unknown)}")
        if gate is not None:
            columns["gate_json"] = gate.model_dump_json()
        if timings is not None:
            for step, column in STEP_COLUMNS.items():
                if (value := getattr(timings, step)) is not None:
                    columns[column] = value
        if not columns:
            return
        assignments = ", ".join(f"{name} = ?" for name in columns)
        with self.connect() as conn:
            conn.execute(
                f"UPDATE night_runs SET {assignments} WHERE run_id = ?",
                [*columns.values(), run_id],
            )

    def get_run(self, run_id: str) -> RunRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM night_runs WHERE run_id = ?", (run_id,)).fetchone()
        return _run(row) if row else None

    def latest_finished_run(self) -> RunRecord | None:
        """The run that finished last; an imported run keeps only its date, so it sorts first
        on that day."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM night_runs WHERE status = 'done' "
                "ORDER BY finished_at DESC, rowid DESC LIMIT 1"
            ).fetchone()
        return _run(row) if row else None

    def active_run(self) -> RunRecord | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM night_runs WHERE status IN (?, ?) ORDER BY rowid DESC LIMIT 1",
                ACTIVE_STATUSES,
            ).fetchone()
        return _run(row) if row else None

    def run_ids(self, prefix: str) -> list[str]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT run_id FROM night_runs WHERE substr(run_id, 1, ?) = ?",
                (len(prefix), prefix),
            ).fetchall()
        return [row["run_id"] for row in rows]

    def fail_unfinished(self, error: str) -> list[str]:
        """Mark runs left queued or running (the process stopped) as failed; their ids."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT run_id FROM night_runs WHERE status IN (?, ?)", ACTIVE_STATUSES
            ).fetchall()
            conn.execute(
                "UPDATE night_runs SET status = 'failed', error = ?, finished_at = ? "
                "WHERE status IN (?, ?)",
                (error, utc_now(), *ACTIVE_STATUSES),
            )
        return [row["run_id"] for row in rows]

    def replace_run(
        self,
        run: RunRecord,
        windows: Iterable[WindowRecord],
        notes: Iterable[NoteRecord],
        report: Sequence[ReportLine] = (),
    ) -> None:
        """Write a whole run in one transaction, dropping an earlier run with the same id."""
        with self.connect() as conn:
            conn.execute("DELETE FROM night_runs WHERE run_id = ?", (run.run_id,))
            _insert_run(conn, run)
            _insert_windows(conn, run.run_id, windows)
            _insert_notes(conn, run.run_id, notes)
            _insert_report(conn, run.run_id, report)

    # ------------------------------------------------------------ windows and notes

    def put_windows(self, run_id: str, windows: Iterable[WindowRecord]) -> None:
        with self.connect() as conn:
            _insert_windows(conn, run_id, windows)

    def put_notes(self, run_id: str, notes: Iterable[NoteRecord]) -> None:
        with self.connect() as conn:
            _insert_notes(conn, run_id, notes)

    def count_windows(
        self, run_id: str, *, line: Line | None = None, flagged: bool | None = None
    ) -> int:
        where, params = _window_filter(run_id, line, flagged)
        with self.connect() as conn:
            row = conn.execute(f"SELECT COUNT(*) FROM windows w WHERE {where}", params).fetchone()
        return int(row[0])

    def windows(
        self,
        run_id: str,
        *,
        line: Line | None = None,
        flagged: bool | None = None,
        sort: WindowSort | None = None,
        offset: int = 0,
        limit: int | None = None,
    ) -> list[StoredWindow]:
        """Windows of a run with their last note, in night order unless sorted."""
        where, params = _window_filter(run_id, line, flagged)
        page = ""
        if limit is not None:
            page = " LIMIT ? OFFSET ?"
            params = [*params, limit, offset]
        with self.connect() as conn:
            rows = conn.execute(
                f"{_WINDOWS_WITH_NOTE} WHERE {where} ORDER BY {_SORTS[sort]}{page}", params
            ).fetchall()
        return [_stored_window(row) for row in rows]

    def window(self, run_id: str, window_id: str) -> StoredWindow | None:
        with self.connect() as conn:
            row = conn.execute(
                f"{_WINDOWS_WITH_NOTE} WHERE w.run_id = ? AND w.window_id = ?",
                (run_id, window_id),
            ).fetchone()
        return _stored_window(row) if row else None

    def notes(self, run_id: str, window_id: str) -> list[NoteRecord]:
        """Every attempt for one window, first attempt first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM notes WHERE run_id = ? AND window_id = ? ORDER BY attempt",
                (run_id, window_id),
            ).fetchall()
        return [_note(row, "") for row in rows]

    def count_notes(self, run_id: str) -> int:
        with self.connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM notes WHERE run_id = ?", (run_id,)).fetchone()
        return int(row[0])

    # ------------------------------------------------------------ reports

    def put_report(self, run_id: str, lines: Sequence[ReportLine]) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM reports WHERE run_id = ?", (run_id,))
            _insert_report(conn, run_id, lines)

    def report(self, run_id: str) -> list[ReportLine]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM reports WHERE run_id = ? ORDER BY position", (run_id,)
            ).fetchall()
        return [
            ReportLine(
                line=row["line"],
                verdict=row["verdict"],
                label=row["label"],
                scanned=row["scanned"],
                flagged=row["flagged"],
                threshold=row["threshold"],
                items=_ITEMS.validate_json(row["items_json"]),
            )
            for row in rows
        ]


# ---------------------------------------------------------------- rows

# The last attempt's note joins each window; a window without a note gets NULLs.
_WINDOWS_WITH_NOTE = """
SELECT w.*, n.attempt AS n_attempt, n.text_raw AS n_text_raw, n.text AS n_text,
       n.passed AS n_passed, n.violations_json AS n_violations_json,
       n.completion_tokens AS n_completion_tokens, n.ms AS n_ms
FROM windows w
LEFT JOIN notes n ON n.run_id = w.run_id AND n.window_id = w.window_id
    AND n.attempt = (
        SELECT MAX(attempt) FROM notes m WHERE m.run_id = w.run_id AND m.window_id = w.window_id
    )
"""


def _window_filter(run_id: str, line: Line | None, flagged: bool | None) -> tuple[str, list[Any]]:
    clauses, params = ["w.run_id = ?"], [run_id]
    if line is not None:
        clauses.append("w.line = ?")
        params.append(line)
    if flagged is not None:
        clauses.append("w.flagged = ?")
        params.append(int(flagged))
    return " AND ".join(clauses), params


def _insert_run(conn: sqlite3.Connection, run: RunRecord) -> None:
    steps = run.step_timings
    conn.execute(
        "INSERT INTO night_runs (run_id, status, step, done, total, max_windows, source, "
        "started_at, finished_at, t_gate, t_facts, t_scan, t_report, t_total, gate_json, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            run.run_id,
            run.status,
            run.step,
            run.done,
            run.total,
            run.max_windows,
            run.source,
            run.started_at,
            run.finished_at,
            steps.gate,
            steps.facts,
            steps.scan,
            steps.report,
            run.total_s,
            run.gate.model_dump_json() if run.gate else None,
            run.error,
        ),
    )


_WINDOW_COLUMNS = (
    "run_id",
    "window_id",
    "seq",
    "ms_id",
    "line",
    "task",
    "group_id",
    "fold",
    "score",
    "flagged",
    "max_abs_z",
    "look_first",
    "facts_json",
    "top_features_json",
)


def _insert_windows(conn: sqlite3.Connection, run_id: str, windows: Iterable[WindowRecord]) -> None:
    conn.executemany(
        # An upsert, not INSERT OR REPLACE: a replaced row would cascade to its notes.
        f"INSERT INTO windows ({', '.join(_WINDOW_COLUMNS)}) "
        f"VALUES ({', '.join('?' for _ in _WINDOW_COLUMNS)}) "
        "ON CONFLICT (run_id, window_id) DO UPDATE SET "
        + ", ".join(f"{name} = excluded.{name}" for name in _WINDOW_COLUMNS[2:]),
        (
            (
                run_id,
                w.window_id,
                w.seq,
                w.ms_id,
                w.line,
                w.task,
                w.group_id,
                w.fold,
                w.score,
                None if w.flagged is None else int(w.flagged),
                w.max_abs_z,
                w.look_first,
                _FACTS.dump_json(list(w.facts)).decode(),
                _FEATURES.dump_json(list(w.top_features)).decode(),
            )
            for w in windows
        ),
    )


def _insert_notes(conn: sqlite3.Connection, run_id: str, notes: Iterable[NoteRecord]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO notes (run_id, window_id, attempt, text_raw, text, passed, "
        "violations_json, completion_tokens, ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                run_id,
                n.window_id,
                n.attempt,
                n.text_raw,
                n.text,
                int(n.passed),
                json.dumps(list(n.violations), ensure_ascii=False),
                n.completion_tokens,
                n.ms,
            )
            for n in notes
        ),
    )


def _insert_report(conn: sqlite3.Connection, run_id: str, lines: Sequence[ReportLine]) -> None:
    conn.executemany(
        "INSERT INTO reports (run_id, line, position, verdict, label, scanned, flagged, "
        "threshold, items_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            (
                run_id,
                line.line,
                position,
                line.verdict,
                line.label,
                line.scanned,
                line.flagged,
                line.threshold,
                _ITEMS.dump_json(line.items).decode(),
            )
            for position, line in enumerate(lines)
        ),
    )


def _run(row: sqlite3.Row) -> RunRecord:
    return RunRecord(
        run_id=row["run_id"],
        status=row["status"],
        source=row["source"],
        step=row["step"],
        done=row["done"],
        total=row["total"],
        max_windows=row["max_windows"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        step_timings=StepTimings(**{step: row[column] for step, column in STEP_COLUMNS.items()}),
        total_s=row["t_total"],
        gate=GateResponse.model_validate_json(row["gate_json"]) if row["gate_json"] else None,
        error=row["error"],
    )


def _stored_window(row: sqlite3.Row) -> StoredWindow:
    window = WindowRecord(
        window_id=row["window_id"],
        line=row["line"],
        task=row["task"],
        seq=row["seq"],
        ms_id=row["ms_id"],
        group_id=row["group_id"],
        fold=row["fold"],
        score=row["score"],
        flagged=None if row["flagged"] is None else bool(row["flagged"]),
        max_abs_z=row["max_abs_z"],
        look_first=row["look_first"],
        facts=tuple(_FACTS.validate_json(row["facts_json"])),
        top_features=tuple(_FEATURES.validate_json(row["top_features_json"])),
    )
    note = _note(row, "n_") if row["n_attempt"] is not None else None
    return StoredWindow(window=window, note=note)


def _note(row: sqlite3.Row, prefix: str) -> NoteRecord:
    return NoteRecord(
        window_id=row["window_id"],
        attempt=row[f"{prefix}attempt"],
        text_raw=row[f"{prefix}text_raw"],
        text=row[f"{prefix}text"],
        passed=bool(row[f"{prefix}passed"]),
        violations=tuple(json.loads(row[f"{prefix}violations_json"])),
        completion_tokens=row[f"{prefix}completion_tokens"],
        ms=row[f"{prefix}ms"],
    )
