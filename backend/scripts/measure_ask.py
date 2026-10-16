"""Measure POST /api/ask latency at several concurrency levels, optionally during a night run.

Standard library only, so it runs in the droplet container without installing anything:
    python3 measure_ask.py --out measure.json [--base-url http://127.0.0.1:8000] \
        [--levels 1,4,8] [--requests 8] [--during-night [--max-windows 300] [--db <DB_PATH>]]

Start the app with RATE_LIMIT_ASK_PER_MIN=0 (and RATE_LIMIT_NIGHT_PER_MIN=0 when a night is
started more than twice a minute); otherwise POST /api/ask answers 429 after 10 questions a
minute and the measurement records rate limits instead of answers.

For every question the script reads the SSE stream and records the time to the first event,
the time until the stream ends, the number of tool steps, the completion tokens from `done`,
the answer code and any error. Each level sends `--requests` questions with that many in
flight at once.

With --during-night it first starts a night (POST /api/night-runs {"max_windows": N}), waits
until the run reaches the scan step and measures while the notes are written. It then waits
for the night to finish and adds its step timings; with --db (the app's DB_PATH) it also counts
the notes that passed the verifier on the first attempt and in the end.

The output JSON holds the UTC time, snapshots of /api/health and /api/system, and per level
the p50, p90 and max of each duration plus the raw measurements.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("measure_ask")

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_LEVELS = (1, 4, 8)
DEFAULT_REQUESTS = 8
REQUEST_TIMEOUT_S = 120.0
NIGHT_TIMEOUT_S = 3600.0
POLL_S = 1.0
# Questions sent in turn: a report question and window questions on both lines (fixtures/).
QUESTIONS: tuple[tuple[str, str | None], ...] = (
    ("What did last night's report find?", None),
    ("Which signal should I check first in this record?", "W-574006"),
    ("Bu kayıtta normalden en çok sapan sinyal hangisi?", "W-246062"),
)
DURATIONS = ("first_event_ms", "done_ms")
NIGHT_FINISHED = ("done", "failed")

# The HTTP client never goes through a proxy: the app runs next to the script.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class ApiClient:
    """JSON and SSE requests to the app."""

    def __init__(self, base_url: str, timeout_s: float = REQUEST_TIMEOUT_S) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s

    def _request(self, method: str, path: str, body: Any = None) -> urllib.request.Request:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"} if data is not None else {}
        return urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )

    def json(self, method: str, path: str, body: Any = None) -> Any:
        with _OPENER.open(self._request(method, path, body), timeout=self.timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    def stream(self, path: str, body: Any) -> Any:
        """The open response; the caller reads its lines and closes it."""
        return _OPENER.open(self._request("POST", path, body), timeout=self.timeout_s)


def parse_sse(lines: Iterable[bytes | str]) -> Iterator[tuple[str, Any]]:
    """(event, data) for every SSE message; `: ping` comments are skipped."""
    event, data = "message", []
    for raw in lines:
        line = (raw.decode("utf-8") if isinstance(raw, bytes) else raw).rstrip("\r\n")
        if not line:
            if data:
                yield event, json.loads("\n".join(data))
            event, data = "message", []
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data.append(line[len("data:") :].strip())
    if data:
        yield event, json.loads("\n".join(data))


def error_of(exc: Exception) -> tuple[int | None, str]:
    """HTTP status and error code (or exception text) of a failed request."""
    if isinstance(exc, urllib.error.HTTPError):
        try:
            code = json.loads(exc.read().decode("utf-8"))["error"]["code"]
        except (ValueError, KeyError, TypeError):
            code = f"http_{exc.code}"
        return exc.code, code
    return None, f"{type(exc).__name__}: {exc}"


def read_stream(
    lines: Iterable[bytes | str], started: float, clock: Callable[[], float] = time.perf_counter
) -> dict[str, Any]:
    """Timings and contents of one /api/ask stream, `started` being when the request went out."""
    record: dict[str, Any] = {
        "first_event_ms": None,
        "done_ms": None,
        "tool_steps": 0,
        "completion_tokens": None,
        "answer": None,
        "answer_source": None,
        "answer_text": None,
        "error": None,
        "events": [],
    }
    for event, data in parse_sse(lines):
        elapsed_ms = round((clock() - started) * 1000, 1)
        if record["first_event_ms"] is None:
            record["first_event_ms"] = elapsed_ms
        record["events"].append({"event": event, "data": data, "ms": elapsed_ms})
        if event == "tool_call":
            record["tool_steps"] += 1
        elif event == "answer":
            record["answer"] = data.get("answer")
            record["answer_source"] = data.get("answer_source")
            record["answer_text"] = data.get("text")
        elif event == "done":
            record["completion_tokens"] = data.get("tokens")
        elif event == "error":
            record["error"] = (data.get("error") or {}).get("code") or "error"
    record["done_ms"] = round((clock() - started) * 1000, 1)
    if record["error"] is None and record["answer"] is None:
        record["error"] = "no_answer"
    return record


def ask_once(client: ApiClient, question: str, window_id: str | None = None) -> dict[str, Any]:
    """Send one question and read its stream; never raises."""
    body: dict[str, Any] = {"question": question}
    if window_id is not None:
        body["window_id"] = window_id
    started = time.perf_counter()
    try:
        with client.stream("/api/ask", body) as response:
            record = read_stream(response, started)
            record["status"] = response.status
    except Exception as exc:  # one failed question must not stop the measurement
        status, error = error_of(exc)
        done_ms = round((time.perf_counter() - started) * 1000, 1)
        record = {"status": status, "error": error, "first_event_ms": None, "done_ms": done_ms}
    return {"question": question, "window_id": window_id, **record}


def percentile(values: Sequence[float], q: float) -> float | None:
    """Linear interpolation between the closest ranks, as numpy's default."""
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return round(ordered[low] + (ordered[high] - ordered[low]) * (position - low), 1)


def summarize(values: Sequence[float | None]) -> dict[str, float | None]:
    present = [value for value in values if value is not None]
    return {
        "n": len(present),
        "p50": percentile(present, 50),
        "p90": percentile(present, 90),
        "max": max(present) if present else None,
    }


def run_level(
    client: ApiClient,
    concurrency: int,
    requests: int,
    questions: Sequence[tuple[str, str | None]] = QUESTIONS,
) -> dict[str, Any]:
    """`requests` questions, `concurrency` at a time, and their percentiles."""
    jobs = [questions[index % len(questions)] for index in range(requests)]
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        measurements = list(pool.map(lambda job: ask_once(client, *job), jobs))
    wall_s = round(time.perf_counter() - started, 2)
    ok = [m for m in measurements if m.get("error") is None]
    level: dict[str, Any] = {
        "concurrency": concurrency,
        "requests": requests,
        "errors": len(measurements) - len(ok),
        "wall_s": wall_s,
    }
    for name in DURATIONS:
        level[name] = summarize([m.get(name) for m in ok])
    level["tool_steps"] = summarize([m.get("tool_steps") for m in ok])
    level["completion_tokens"] = summarize([m.get("completion_tokens") for m in ok])
    level["measurements"] = measurements
    return level


def snapshot(client: ApiClient, path: str) -> Any:
    try:
        return client.json("GET", path)
    except Exception as exc:
        status, error = error_of(exc)
        return {"error": error, "status": status}


def wait_for_run(
    client: ApiClient,
    run_id: str,
    until: Callable[[dict[str, Any]], bool],
    timeout_s: float,
    poll_s: float = POLL_S,
) -> dict[str, Any]:
    """Poll the run until `until(progress)` holds or it finishes; the last progress seen."""
    deadline = time.monotonic() + timeout_s
    while True:
        progress = client.json("GET", f"/api/night-runs/{run_id}")
        if until(progress) or progress.get("status") in NIGHT_FINISHED:
            return progress
        if time.monotonic() >= deadline:
            raise TimeoutError(f"night run {run_id} still {progress.get('status')} after wait")
        time.sleep(poll_s)


def note_counts(db_path: str | Path, run_id: str) -> dict[str, int] | None:
    """Notes of a run in the store: windows with a note, passed on the first attempt, passed
    in the end (the retry passed) and retried. None when the store file is missing."""
    path = Path(db_path)
    if not path.is_file():
        return None
    conn = sqlite3.connect(path, timeout=10)
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT window_id),"
            " COUNT(DISTINCT CASE WHEN attempt = 1 AND passed = 1 THEN window_id END),"
            " COUNT(DISTINCT CASE WHEN passed = 1 THEN window_id END),"
            " COUNT(DISTINCT CASE WHEN attempt > 1 THEN window_id END)"
            " FROM notes WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    finally:
        conn.close()
    windows, first, final, retried = row
    return {
        "windows": windows,
        "passed_first_attempt": first,
        "passed_final": final,
        "retried": retried,
    }


def measure(args: argparse.Namespace, client: ApiClient) -> tuple[dict[str, Any], bool]:
    result: dict[str, Any] = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": client.base_url,
        "health": snapshot(client, "/api/health"),
        "system": snapshot(client, "/api/system"),
        "settings": {
            "levels": list(args.levels),
            "requests_per_level": args.requests,
            "during_night": args.during_night,
            "questions": [{"question": q, "window_id": w} for q, w in QUESTIONS],
        },
        "levels": [],
        "night": None,
    }
    night: dict[str, Any] | None = None
    if args.during_night:
        started = client.json("POST", "/api/night-runs", {"max_windows": args.max_windows})
        night = result["night"] = {"run_id": started["run_id"], "max_windows": args.max_windows}
        logger.info("night %s started; waiting for the scan step", night["run_id"])
        progress = wait_for_run(
            client, night["run_id"], lambda p: p.get("step") == "scan", args.night_timeout
        )
        if progress.get("step") != "scan" or progress.get("status") in NIGHT_FINISHED:
            night.update(progress=progress, error="the night finished before its scan step")
            return result, False

    for concurrency in args.levels:
        before = snapshot(client, f"/api/night-runs/{night['run_id']}") if night else None
        level = run_level(client, concurrency, args.requests)
        if night is not None:
            after = snapshot(client, f"/api/night-runs/{night['run_id']}")
            level["night_step"] = {"before": before.get("step"), "after": after.get("step")}
        logger.info(
            "concurrency %d: first event p50 %s ms, done p50 %s ms, %d errors",
            concurrency,
            level["first_event_ms"]["p50"],
            level["done_ms"]["p50"],
            level["errors"],
        )
        result["levels"].append(level)

    if night is not None:
        progress = wait_for_run(client, night["run_id"], lambda _: False, args.night_timeout)
        night.update(
            status=progress.get("status"),
            total=progress.get("total"),
            timings_s=progress.get("timings_s"),
            notes=note_counts(args.db, night["run_id"]) if args.db else None,
        )
        logger.info("night %s %s: %s", night["run_id"], night["status"], night["timings_s"])
    errors = sum(level["errors"] for level in result["levels"])
    return result, errors == 0 and (night is None or night.get("status") == "done")


def levels(text: str) -> tuple[int, ...]:
    values = tuple(int(part) for part in text.split(",") if part.strip())
    if not values or min(values) < 1:
        raise argparse.ArgumentTypeError("levels are whole numbers >= 1, e.g. 1,4,8")
    return values


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", type=Path, required=True, help="JSON file to write")
    parser.add_argument("--levels", type=levels, default=DEFAULT_LEVELS, help="e.g. 1,4,8")
    parser.add_argument("--requests", type=int, default=DEFAULT_REQUESTS, help="per level")
    parser.add_argument("--during-night", action="store_true", help="measure during a night")
    parser.add_argument("--max-windows", type=int, default=300, help="windows of that night")
    parser.add_argument("--db", help="the app's DB_PATH, for the note counts of the night")
    parser.add_argument("--timeout", type=float, default=REQUEST_TIMEOUT_S, help="per request")
    parser.add_argument("--night-timeout", type=float, default=NIGHT_TIMEOUT_S)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    result, ok = measure(args, ApiClient(args.base_url, args.timeout))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", "utf-8")
    logger.info("written %s", args.out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
