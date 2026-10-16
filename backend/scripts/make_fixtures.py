"""Regenerate fixtures/ from a finished night run in the SQLite store.

Run from backend/:
    uv run python -m scripts.make_fixtures --db <DB_PATH> [--run <run_id>] --out ../fixtures

Without `--run` the run that finished last is used, as the API does. The night's responses are
read with the code the API serves them with (`telereason.store.night`, NIGHT_SOURCE=db), so a
fixture is the API's answer for that run: the report, the gate, the first page of windows, the
windows the report and the `/api/ask` sample open plus one unflagged window on a scored line,
`system.last_night` and the run's progress.

What a night run does not store is kept from the fixtures already in `--out`: `health.json`,
`examples.json`, `night_run_started.json`, `gpu` and `versions` in `system.json` and the `done`
event of `ask.sse`.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from telereason.api.night_runs import progress
from telereason.api.schemas import DoneEvent, ReportResponse, SystemResponse, WindowDetail
from telereason.store.db import NightDb, RunRecord
from telereason.store.night import DbNight, RunWindows

logger = logging.getLogger("make_fixtures")

WINDOWS_PAGE_SIZE = 20

ASK_QUESTION = "What went wrong in this record?"
ASK_TOOL = "classify_fault"
ASK_SUMMARY = "diagnosis closed on this line"

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
  `window_detail.json`, `night_run.json`, `last_night` in `system.json`, the window of
  `ask_request.json` and `ask.sse` (the first report window on the closed line) and the AUROC
  interval in the `ask.sse` answer.
- Window ids are the store's, so each is unique. A note is shown only when it passed the
  verifier; otherwise `note` is empty and `verified` is false. `top_features` is shown on AURSAD
  only and is empty when the run stored none.
- `plain`: |z| < 2 within the normal range, 2 to 3 slightly off normal, 3 or more clearly off
  normal.
- Kept from the previous fixtures, because a night run does not store them: `health.json`,
  `examples.json`, `night_run_started.json`, `gpu` and `versions` in `system.json` and the `done`
  event of `ask.sse`. They come from the 11 Oct MI300X run: the model, the gate's XGBoost
  version, `memory_total_gib` (the card's memory), `memory_used_gib` (card peak in the load test
  while the engine was loaded, MiB / 1024), `total_ms` (single-question p90 alone on the card)
  and `tokens` (mean tokens per question with one user). `ms` is `null` because tool time was
  not measured.
- Version strings other than `xgboost`, labels, explanations and example questions:
  `docs/api.md`.

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


def read_sse(text: str) -> dict[str, Any]:
    """The data of each event in a recorded SSE stream, by event name."""
    events = {}
    for block in text.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
        events[fields["event"]] = json.loads(fields["data"])
    return events


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


def build(db: NightDb, run: RunRecord, out: Path) -> dict[str, str]:
    """The text of every file to write, by name."""
    try:
        system = SystemResponse.model_validate_json(
            (out / "system.json").read_text(encoding="utf-8")
        )
        stream = read_sse((out / "ask.sse").read_text(encoding="utf-8"))
        done = DoneEvent.model_validate(stream["done"])
    except (OSError, KeyError) as exc:
        raise FixtureError(f"{out} needs the previous system.json and ask.sse: {exc}") from exc

    night = DbNight(db)
    windows = RunWindows(db, run)
    gate = night.gate(run)
    report = night.report(run)
    closed_line, ask_window = closed_window(report)
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
    files["ask.sse"] = ask_stream(closed_gate.auroc, low, high, ask_window, done)
    files["README.md"] = README.format(
        run_id=run.run_id,
        date=(run.finished_at or "")[:10],
        source=SOURCES[run.source],
        db_name=db.path.name,
        page_size=WINDOWS_PAGE_SIZE,
        n_detail=len(details),
        unflagged=unflagged,
        detail_mix=detail_mix(details),
    )
    return files


def detail_mix(details: dict[str, WindowDetail]) -> str:
    """Describe the detail windows, e.g. '10 flagged AURSAD, 1 unflagged AURSAD, 10 FactoryWave'."""
    kinds = {True: "flagged ", False: "unflagged ", None: ""}
    counts = Counter(f"{kinds[w.flagged]}{w.line}" for w in details.values())
    order = [f"{kind}{line}" for line in ("AURSAD", "FactoryWave") for kind in kinds.values()]
    return ", ".join(f"{counts[name]} {name}" for name in order if counts[name])


def make_fixtures(db: NightDb, run_id: str | None, out: Path) -> RunRecord:
    run = pick_run(db, run_id)
    files = build(db, run, out)
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    logger.info("Wrote %d files of run %s to %s", len(files), run.run_id, out)
    return run


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True, help="SQLite store of night runs")
    parser.add_argument("--run", help="run id; default: the run that finished last")
    parser.add_argument("--out", type=Path, required=True, help="fixtures directory to update")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        make_fixtures(NightDb(args.db), args.run, args.out)
    except FixtureError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
