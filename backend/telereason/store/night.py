"""API responses of the stored night (NIGHT_SOURCE=db): the latest finished run in the store.

`DbNight` answers `/api/report/latest`, `/api/gate`, `/api/system.last_night` and the window
endpoints with the models of `api/schemas.py`, so no schema changes with the source. A missing
store or a store without a finished run raises `DataNotAvailableError` (503).
"""

from __future__ import annotations

from telereason.api.schemas import (
    GateResponse,
    LastNight,
    Line,
    ReportResponse,
    Verdict,
    WindowDetail,
    WindowListResponse,
    WindowSummary,
)
from telereason.data.loader import DataNotAvailableError, WindowNotFoundError
from telereason.night.report import has_score
from telereason.store.db import NightDb, RunRecord, StoredWindow, WindowSort

NO_NIGHT_MESSAGE = "No finished night run in the store yet; import or run a night first."


class RunWindows:
    """The windows of one finished run, with the same methods as `DataStore`."""

    def __init__(self, db: NightDb, run: RunRecord) -> None:
        self.db = db
        self.run = run
        gate = run.gate.lines if run.gate else []
        self.verdicts: dict[Line, Verdict] = {row.line: row.verdict for row in gate}

    def verdict(self, line: Line) -> Verdict:
        # A line the gate did not judge gets no diagnosis and no score.
        return self.verdicts.get(line, "closed")

    def summary(self, stored: StoredWindow) -> WindowSummary:
        window = stored.window
        scored = has_score(self.verdict(window.line))
        return WindowSummary(
            window_id=window.window_id,
            line=window.line,
            task=window.task,
            score=window.score if scored else None,
            flagged=window.flagged if scored else None,
            look_first=window.look_first,
            verified=stored.verified,
        )

    def detail(self, stored: StoredWindow) -> WindowDetail:
        window = stored.window
        verdict = self.verdict(window.line)
        scored = has_score(verdict)
        return WindowDetail(
            window_id=window.window_id,
            line=window.line,
            task=window.task,
            verdict=verdict,
            score=window.score if scored else None,
            flagged=window.flagged if scored else None,
            look_first=window.look_first,
            facts=list(window.facts),
            # Gate contributions are shown on AURSAD only (docs/api.md).
            top_features=list(window.top_features) if window.line == "AURSAD" else [],
            # Only a verified note is shown; otherwise the facts stand alone.
            note=stored.note.text if stored.verified and stored.note else "",
            verified=stored.verified,
        )

    def list_windows(
        self,
        *,
        line: Line | None = None,
        flagged: bool | None = None,
        sort: WindowSort | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> WindowListResponse:
        run_id = self.run.run_id
        rows = self.db.windows(
            run_id,
            line=line,
            flagged=flagged,
            sort=sort,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
        return WindowListResponse(
            page=page,
            page_size=page_size,
            total=self.db.count_windows(run_id, line=line, flagged=flagged),
            items=[self.summary(row) for row in rows],
        )

    def get_window(self, window_id: str) -> WindowDetail:
        stored = self.db.window(self.run.run_id, window_id)
        if stored is None:
            raise WindowNotFoundError(window_id)
        return self.detail(stored)


class DbNight:
    """The night the API serves: always the run that finished last."""

    def __init__(self, db: NightDb) -> None:
        self.db = db

    def latest(self) -> RunRecord:
        # Reading must not create the file: an absent store has no night.
        run = self.db.latest_finished_run() if self.db.path.exists() else None
        if run is None:
            raise DataNotAvailableError(NO_NIGHT_MESSAGE)
        return run

    def latest_or_none(self) -> RunRecord | None:
        try:
            return self.latest()
        except DataNotAvailableError:
            return None

    def windows(self) -> RunWindows:
        return RunWindows(self.db, self.latest())

    def gate(self, run: RunRecord | None = None) -> GateResponse:
        run = run or self.latest()
        if run.gate is None:
            raise DataNotAvailableError(f"Night run {run.run_id} has no gate verdicts.")
        return run.gate

    def report(self, run: RunRecord | None = None) -> ReportResponse:
        run = run or self.latest()
        if run.timings is None:
            raise DataNotAvailableError(f"Night run {run.run_id} has no timings.")
        return ReportResponse(
            run_id=run.run_id, timings_s=run.timings, lines=self.db.report(run.run_id)
        )

    def last_night(self, run: RunRecord | None = None) -> LastNight | None:
        run = run or self.latest_or_none()
        if run is None or run.timings is None:
            return None
        return LastNight(run_id=run.run_id, timings_s=run.timings)

    # The window endpoints call these on `app.state.night_store`, as on `DataStore`.

    def list_windows(
        self,
        *,
        line: Line | None = None,
        flagged: bool | None = None,
        sort: WindowSort | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> WindowListResponse:
        return self.windows().list_windows(
            line=line, flagged=flagged, sort=sort, page=page, page_size=page_size
        )

    def get_window(self, window_id: str) -> WindowDetail:
        return self.windows().get_window(window_id)
