"""The night run on request (#49): one run at a time, its steps in the fixed order.

`NightRunner.start` records a queued run and runs it in a task on the app's event loop:
gate, facts, scan, report, as on 11 Oct (`p35_gece.py::kip_gece`). Each step is a method of
a `NightSteps` object, so the runner only orders the steps, times them, keeps the progress in
the store and marks the run done or failed. The runner never opens the engine; it receives the
one the app opened (`ai/engine.py`).

Until the real steps are wired (#40), `PendingSteps` fails a run at its first step.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from telereason.ai.engine import Engine
from telereason.api.schemas import NightStep, StepTimings
from telereason.store.db import NightDb, RunRecord, utc_now

logger = logging.getLogger(__name__)

STEPS: tuple[NightStep, ...] = ("gate", "facts", "scan", "report")
# Progress goes to the store at most this often; the UI polls once a second.
PROGRESS_INTERVAL_S = 1.0
INTERRUPTED_MESSAGE = "The server stopped during this night run."


class RunnerBusyError(RuntimeError):
    """A night run is already queued or running."""


@dataclass
class RunContext:
    """What a step gets: the run, the store, the engine, and room to pass results on."""

    run_id: str
    max_windows: int
    db: NightDb
    engine: Engine
    # Results one step leaves for the next (for example the gate verdicts for the scan).
    state: dict[str, Any] = field(default_factory=dict)
    done: int = 0
    total: int = 0
    clock: Callable[[], float] = time.monotonic
    _written_at: float = field(default=float("-inf"), repr=False)

    def progress(self, done: int, total: int | None = None, *, flush: bool = False) -> None:
        """Windows processed in the current step; written at most every PROGRESS_INTERVAL_S."""
        self.done = done
        if total is not None:
            self.total = total
        now = self.clock()
        if flush or now - self._written_at >= PROGRESS_INTERVAL_S:
            self._written_at = now
            self.db.update_run(self.run_id, done=self.done, total=self.total)


class NightSteps(Protocol):
    async def gate(self, ctx: RunContext) -> None: ...

    async def facts(self, ctx: RunContext) -> None: ...

    async def scan(self, ctx: RunContext) -> None: ...

    async def report(self, ctx: RunContext) -> None: ...


class PendingSteps:
    """Placeholder until the real steps are wired (#40): every step refuses to run."""

    async def _pending(self, ctx: RunContext) -> None:
        raise NotImplementedError("the night steps are not wired yet (#40)")

    gate = facts = scan = report = _pending


def run_id_prefix(now: datetime | None = None) -> str:
    return f"run-{(now or datetime.now(UTC)):%Y%m%d}-"


class NightRunner:
    def __init__(self, db: NightDb, engine: Engine, steps: NightSteps | None = None) -> None:
        self.db = db
        self.engine = engine
        self.steps: NightSteps = steps or PendingSteps()
        self.task: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()

    def recover(self) -> list[str]:
        """At startup: a run left queued or running by a stopped process is failed."""
        if not self.db.path.exists():
            return []
        failed = self.db.fail_unfinished(INTERRUPTED_MESSAGE)
        if failed:
            logger.warning("night runs left unfinished, now failed: %s", ", ".join(failed))
        return failed

    def get(self, run_id: str) -> RunRecord | None:
        # Reading must not create the file: an absent store has no runs.
        return self.db.get_run(run_id) if self.db.path.exists() else None

    def start(self, max_windows: int) -> RunRecord:
        """Record a queued run and start it; raises RunnerBusyError while one is active.

        No await between the check and the task: on one event loop two requests cannot both
        pass the check.
        """
        self.db.init()
        if self.active or self.db.active_run() is not None:
            raise RunnerBusyError("A night run is already active.")
        run = RunRecord(
            run_id=self._next_run_id(),
            status="queued",
            source="live",
            total=max_windows,
            max_windows=max_windows,
        )
        self.db.create_run(run)
        self.task = asyncio.create_task(self._run(run), name=f"night-run {run.run_id}")
        logger.info("night run %s queued (max_windows=%d)", run.run_id, max_windows)
        return run

    async def wait(self) -> None:
        """Until the active run, if any, has finished."""
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)

    async def close(self) -> None:
        """At shutdown: cancel an active run; it is marked failed."""
        if self.active and self.task is not None:
            self.task.cancel()
            await self.wait()

    def _next_run_id(self) -> str:
        prefix = run_id_prefix()
        numbers = [
            int(tail)
            for run_id in self.db.run_ids(prefix)
            if (tail := run_id[len(prefix) :]).isdigit()
        ]
        return f"{prefix}{max(numbers, default=0) + 1}"

    async def _run(self, run: RunRecord) -> None:
        ctx = RunContext(
            run_id=run.run_id,
            max_windows=run.max_windows or run.total,
            db=self.db,
            engine=self.engine,
            total=run.total,
        )
        timings = StepTimings()
        started = time.perf_counter()
        self.db.update_run(run.run_id, status="running", started_at=utc_now())
        try:
            for step in STEPS:
                self.db.update_run(run.run_id, step=step)
                step_started = time.perf_counter()
                await getattr(self.steps, step)(ctx)
                setattr(timings, step, time.perf_counter() - step_started)
                ctx.progress(ctx.done, flush=True)
                self.db.update_run(run.run_id, timings=timings)
                logger.info("night run %s: %s in %.1f s", run.run_id, step, getattr(timings, step))
        except asyncio.CancelledError:
            self._fail(run.run_id, INTERRUPTED_MESSAGE)
            raise
        except Exception as exc:
            logger.exception("night run %s failed", run.run_id)
            self._fail(run.run_id, f"{type(exc).__name__}: {exc}")
            return
        total_s = time.perf_counter() - started
        self.db.update_run(run.run_id, status="done", finished_at=utc_now(), t_total=total_s)
        logger.info("night run %s done in %.1f s", run.run_id, total_s)

    def _fail(self, run_id: str, error: str) -> None:
        self.db.update_run(run_id, status="failed", finished_at=utc_now(), error=error)
