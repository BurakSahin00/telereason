"""Regenerate fixtures/ from a finished night run in the SQLite store.

Run from backend/:
    uv run python -m scripts.make_fixtures --db <DB_PATH> [--run <run_id>] --out ../fixtures \
        [--system-json <saved GET /api/system>] [--ask-sse <recorded POST /api/ask stream>]

Without `--run` the run that finished last is used, as the API does. The night's responses are
read with the code the API serves them with (`telereason.store.night`, NIGHT_SOURCE=db), so a
fixture is the API's answer for that run: the report, the gate, the first page of windows, the
windows the report and the `/api/ask` sample open plus one unflagged window on a scored line,
`system.last_night` and the run's progress.

What a night run does not store is kept from the fixtures already in `--out`: `health.json`,
`examples.json`, `night_run_started.json`, `gpu` and `versions` in `system.json` and the `done`
event of `ask.sse`. `--system-json` takes `gpu` and `versions` from a saved `/api/system`
response instead, and `--ask-sse` takes `ask.sse` from a recorded `/api/ask` stream; then
`ask_request.json` asks about the stream's window, which must be a closed-line window of the run.
Both files are checked against the API models and the script stops on a mismatch.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from telereason.api.night_runs import progress
from telereason.api.schemas import (
    AnswerEvent,
    DoneEvent,
    ErrorResponse,
    ReportResponse,
    SystemResponse,
    ToolCallEvent,
    ToolResultEvent,
    WindowDetail,
)
from telereason.data.loader import WindowNotFoundError
from telereason.store.db import NightDb, RunRecord
from telereason.store.night import DbNight, RunWindows

logger = logging.getLogger("make_fixtures")

WINDOWS_PAGE_SIZE = 20

ASK_QUESTION = "What went wrong in this record?"
ASK_TOOL = "classify_fault"
ASK_SUMMARY = "diagnosis closed on this line"
SSE_EVENTS: dict[str, type[BaseModel]] = {
    "tool_call": ToolCallEvent,
    "tool_result": ToolResultEvent,
    "answer": AnswerEvent,
    "done": DoneEvent,
    "error": ErrorResponse,
}
KEPT = "kept from the previous fixtures"

SOURCES = {
    "import": "imported from the results of the 11 Oct MI300X night (`scripts.import_night`)",
    "live": "run by the app (`POST /api/night-runs`)",
}

README = """\
# Fixtures

Example API responses of one night run, read from the SQLite store of night runs.

- Run: `{run_id}`
- Date: {date}
- Source: store `{db_name}`; the run was {source}

Do not edit by hand; regenerate from `backend/`:

```bash
uv run python -m scripts.make_fixtures --db <DB_PATH> --run {run_id} --out ../fixtures
```

`backend/tests/test_fixtures.py` validates every file against the models in
`backend/telereason/api/schemas.py`.

| File | Endpoint |
|---|---|
| `health.json` | `GET /api/health` |
| `system.json` | `GET /api/system` |
| `gate.json` | `GET /api/gate` |
| `report_latest.json` | `GET /api/report/latest` |
| `windows.json` | `GET /api/windows?page=1&page_size={page_size}` (no filter, night order) |
| `window_detail.json` | `GET /api/windows/{{window_id}}`: object keyed by window id |
| `ask_request.json` | `POST /api/ask` request body |
| `ask.sse` | `POST /api/ask` response stream |
| `examples.json` | `GET /api/examples` |
| `night_run_started.json` | `POST /api/night-runs` (202) |
| `night_run.json` | `GET /api/night-runs/{{run_id}}` (run `{run_id}`) |
| pending (#28) | `GET /api/windows/{{window_id}}/signals` (needs raw time series) |

`window_detail.json` holds {n_detail} windows: every window listed in `report_latest.json`
and the `ask_request.json` window{unflagged}.
By line: {detail_mix}.
`windows.json` is one page, so most of these windows are not in it; in fake mode
`GET /api/windows/{{window_id}}` reads `window_detail.json` first.

## Where the values come from

- Run `{run_id}` in the store, read the way the API reads it with `NIGHT_SOURCE=db`
  (`telereason.store.night`): `report_latest.json`, `gate.json`, `windows.json`,
  `window_detail.json`, `night_run.json`, `last_night` in `system.json` and the window of
  `ask_request.json` ({ask_window_source}).
- Window ids are the store's, so each is unique. A note is shown only when it passed the
  verifier; otherwise `note` is empty and `verified` is false. `top_features` is shown on AURSAD
  only and is empty when the run stored none.
- `plain`: |z| < 2 within the normal range, 2 to 3 slightly off normal, 3 or more clearly off
  normal.
- `health.json`, `examples.json` and `night_run_started.json`: kept from the previous fixtures,
  because a night run does not store them. Example questions: `docs/api.md`.
- `gpu` and `versions` in `system.json`: {system_source}.
- `ask.sse`: {ask_source}.
- The first fixtures took the kept values from the 11 Oct MI300X run: the model, the gate's
  XGBoost version, `memory_total_gib` (the card's memory), `memory_used_gib` (card peak in the
  load test while the engine was loaded, MiB / 1024; vLLM reserves 85% of GPU memory for its
  cache, so this is not what the model needs), `total_ms` (single-question p90 alone on
  the card) and `tokens` (mean tokens per question with one user); `ms` was `null` because tool
  time was not measured. Their other version strings: `docs/api.md`.

Contains small samples derived from FactoryBench (license: see `DATA_NOTICE.md`).
"""


class FixtureError(RuntimeError):
    """The store or the fixtures directory cannot give a full set of fixtures."""


def pick_run(db: NightDb, run_id: str | None) -> RunRecord:
    """The finished run `run_id`, or the run that finished last."""
    if not db.path.is_file():
        raise FixtureError(f"{db.path} does not exist")
    run = db.latest_finished_run() if run_id is None else db.get_run(run_id)
    if run is None:
        which = "finished night run" if run_id is None else f"night run {run_id}"
        raise FixtureError(f"no {which} in {db.path}")
    if run.status != "done":
        raise FixtureError(f"night run {run.run_id} is {run.status}, not done")
    return run


def read_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FixtureError(str(exc)) from exc


def file_time(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).strftime("%Y-%m-%d %H:%M UTC")


def checked[M: BaseModel](model: type[M], data: Any, what: str) -> M:
    """`data` as `model`; extra, missing or coerced fields are a mismatch, as in the API tests."""
    try:
        parsed = model.model_validate(data)
    except ValidationError as exc:
        raise FixtureError(f"{what} does not match {model.__name__}: {exc}") from exc
    if parsed.model_dump(mode="json") != data:
        raise FixtureError(f"{what} has fields or values that {model.__name__} does not")
    return parsed


def read_stream(text: str, what: str) -> list[tuple[str, BaseModel]]:
    """The messages of an `/api/ask` SSE stream, checked against the event models; `: ping`
    comments are dropped."""
    messages: list[tuple[str, BaseModel]] = []
    for block in text.replace("\r\n", "\n").strip().split("\n\n"):
        fields: dict[str, str] = {}
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, sep, value = line.partition(": ")
            if not sep or name not in ("event", "data") or name in fields:
                raise FixtureError(f"{what}: not an SSE message: {block!r}")
            fields[name] = value
        if not fields:
            continue
        if set(fields) != {"event", "data"} or fields["event"] not in SSE_EVENTS:
            raise FixtureError(f"{what}: not an /api/ask event: {block!r}")
        event = fields["event"]
        try:
            data = json.loads(fields["data"])
        except json.JSONDecodeError as exc:
            raise FixtureError(f"{what}: {event} data is not JSON: {exc}") from exc
        messages.append((event, checked(SSE_EVENTS[event], data, f"{what}: {event}")))
    return messages


def recorded_stream(path: Path) -> tuple[str, str | None]:
    """A real `/api/ask` answer, written again without pings, and the window it asks about."""
    messages = read_stream(read_file(path), str(path))
    events = [event for event, _ in messages]
    if "error" in events or "answer" not in events or events[-1:] != ["done"]:
        raise FixtureError(f"{path}: expected an answer and a final done event, got {events}")
    called = {
        data.args["window_id"]
        for _, data in messages
        if isinstance(data, ToolCallEvent) and "window_id" in data.args
    }
    if len(called) > 1:
        raise FixtureError(f"{path} asks about more than one window: {sorted(called)}")
    text = "".join(sse_message(event, data.model_dump(mode="json")) for event, data in messages)
    return text, next(iter(called), None)


def recorded_window(windows: RunWindows, window_id: str, path: Path) -> WindowDetail:
    """The window a recorded stream asks about; it must be a closed-line window of the run."""
    try:
        window = windows.get_window(window_id)
    except WindowNotFoundError as exc:
        raise FixtureError(
            f"{path} asks about {window_id}, not in run {windows.run.run_id}"
        ) from exc
    if window.verdict != "closed":
        raise FixtureError(f"{path} asks about {window_id} on {window.line}, not a closed line")
    return window


def saved_system(path: Path) -> SystemResponse:
    try:
        data = json.loads(read_file(path))
    except json.JSONDecodeError as exc:
        raise FixtureError(f"{path} is not JSON: {exc}") from exc
    system = checked(SystemResponse, data, str(path))
    if system.gpu is None:
        raise FixtureError(f"{path} has no GPU")
    return system


def sse_message(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def ask_stream(gate_auroc: float, low: float, high: float, window_id: str, done: DoneEvent) -> str:
    text = (
        "Diagnosis is closed on this line: on new days, fault prediction is no better than "
        f"chance (AUROC {gate_auroc:.3f}, interval {low:.3f} to {high:.3f}). "
        "Only verified facts are reported for this window."
    )
    return "".join(
        sse_message(event, data)
        for event, data in (
            ("tool_call", {"turn": 1, "tool": ASK_TOOL, "args": {"window_id": window_id}}),
            ("tool_result", {"turn": 1, "tool": ASK_TOOL, "summary": ASK_SUMMARY, "ms": None}),
            ("answer", {"answer": "DIAGNOSIS_CLOSED", "answer_source": "tool", "text": text}),
            ("done", done.model_dump(mode="json")),
        )
    )


def closed_window(report: ReportResponse) -> tuple[str, str]:
    """The line and the first report window of the first closed line: the `/api/ask` sample."""
    for line in report.lines:
        if line.verdict == "closed" and line.items:
            return line.line, line.items[0].window_id
    raise FixtureError(f"run {report.run_id} reports no window on a closed line")


def build(
    db: NightDb,
    run: RunRecord,
    out: Path,
    system_json: Path | None = None,
    ask_sse: Path | None = None,
) -> dict[str, str]:
    """The text of every file to write, by name."""
    if system_json is None:
        system = SystemResponse.model_validate_json(read_file(out / "system.json"))
        system_source = KEPT
    else:
        system = saved_system(system_json)
        served = system.last_night.run_id if system.last_night else "none"
        if served != run.run_id:
            logger.warning("%s was saved while run %s was served", system_json, served)
        system_source = (
            f"saved `GET /api/system` response `{system_json.name}` (run `{served}`, file time "
            f"{file_time(system_json)}): the GPU as `rocm-smi` reported it and the installed "
            "versions"
        )

    night = DbNight(db)
    windows = RunWindows(db, run)
    gate = night.gate(run)
    report = night.report(run)
    closed_line, ask_window = closed_window(report)
    ask_window_source = "the first report window on the closed line"
    recorded = recorded_stream(ask_sse) if ask_sse is not None else None
    if ask_sse is not None and recorded is not None and recorded[1] is not None:
        # The stream was recorded for its own request, so `ask_request.json` follows it.
        closed_line = recorded_window(windows, recorded[1], ask_sse).line
        ask_window = recorded[1]
        ask_window_source = "the closed-line window the recorded `ask.sse` asks about"
    closed_gate = next(row for row in gate.lines if row.line == closed_line)
    low, high = closed_gate.auroc_ci

    # Every report window and the ask window open in fake mode; one unflagged window on a
    # scored line is added so the UI also shows a window below the threshold.
    picked = [item.window_id for line in report.lines for item in line.items]
    picked.append(ask_window)
    unflagged = ""
    for line in (line.line for line in report.lines if line.threshold is not None):
        below = windows.list_windows(line=line, flagged=False, page_size=1).items
        if below:
            picked.append(below[0].window_id)
            unflagged = f", plus one unflagged {line} window"
            break
    details = {wid: windows.get_window(wid) for wid in dict.fromkeys(picked)}

    models: dict[str, BaseModel] = {
        "system.json": system.model_copy(update={"last_night": night.last_night(run)}),
        "gate.json": gate,
        "report_latest.json": report,
        "windows.json": windows.list_windows(page=1, page_size=WINDOWS_PAGE_SIZE),
        "night_run.json": progress(run),
    }
    data: dict[str, Any] = {name: model.model_dump(mode="json") for name, model in models.items()}
    data["window_detail.json"] = {wid: d.model_dump(mode="json") for wid, d in details.items()}
    data["ask_request.json"] = {"question": ASK_QUESTION, "window_id": ask_window}
    files = {
        name: json.dumps(value, indent=2, ensure_ascii=False) + "\n" for name, value in data.items()
    }
    if ask_sse is None or recorded is None:
        previous = dict(read_stream(read_file(out / "ask.sse"), "previous ask.sse"))
        done = previous.get("done")
        if not isinstance(done, DoneEvent):
            raise FixtureError("the previous ask.sse has no done event")
        files["ask.sse"] = ask_stream(closed_gate.auroc, low, high, ask_window, done)
        ask_source = (
            f"the `done` event {KEPT}; the window and the AUROC interval in the answer from run "
            f"`{run.run_id}`"
        )
    else:
        files["ask.sse"] = recorded[0]
        ask_source = (
            f"recorded `POST /api/ask` stream `{ask_sse.name}` for `ask_request.json` (run "
            f"`{run.run_id}`, file time {file_time(ask_sse)}): one real answer, so `total_ms`, "
            "`tokens` and `ms` are that answer's"
        )
    files["README.md"] = README.format(
        run_id=run.run_id,
        date=(run.finished_at or "")[:10],
        source=SOURCES[run.source],
        db_name=db.path.name,
        page_size=WINDOWS_PAGE_SIZE,
        n_detail=len(details),
        unflagged=unflagged,
        detail_mix=detail_mix(details),
        system_source=system_source,
        ask_source=ask_source,
        ask_window_source=ask_window_source,
    )
    return files


def detail_mix(details: dict[str, WindowDetail]) -> str:
    """Describe the detail windows, e.g. '10 flagged AURSAD, 1 unflagged AURSAD, 10 FactoryWave'."""
    kinds = {True: "flagged ", False: "unflagged ", None: ""}
    counts = Counter(f"{kinds[w.flagged]}{w.line}" for w in details.values())
    order = [f"{kind}{line}" for line in ("AURSAD", "FactoryWave") for kind in kinds.values()]
    return ", ".join(f"{counts[name]} {name}" for name in order if counts[name])


def make_fixtures(
    db: NightDb,
    run_id: str | None,
    out: Path,
    system_json: Path | None = None,
    ask_sse: Path | None = None,
) -> RunRecord:
    run = pick_run(db, run_id)
    # Every input is read and checked before the first file is written.
    files = build(db, run, out, system_json, ask_sse)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    logger.info("Wrote %d files of run %s to %s", len(files), run.run_id, out)
    return run


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True, help="SQLite store of night runs")
    parser.add_argument("--run", help="run id; default: the run that finished last")
    parser.add_argument("--out", type=Path, required=True, help="fixtures directory to update")
    parser.add_argument("--system-json", type=Path, help="saved GET /api/system response")
    parser.add_argument("--ask-sse", type=Path, help="recorded POST /api/ask stream")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        make_fixtures(NightDb(args.db), args.run, args.out, args.system_json, args.ask_sse)
    except FixtureError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
