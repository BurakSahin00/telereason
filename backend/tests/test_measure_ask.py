"""scripts/measure_ask.py: SSE parsing and timings, and runs against the fake-mode app (#51)."""

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import uvicorn

from scripts.measure_ask import (
    ApiClient,
    ask_once,
    main,
    note_counts,
    parse_sse,
    percentile,
    read_stream,
    summarize,
    wait_for_run,
)
from telereason.api import fake
from telereason.api.main import create_app
from telereason.settings import DEFAULT_MODEL, Settings
from telereason.store.db import NightDb, RunRecord
from tests.test_store import note, window

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"
ASK_SSE = (FIXTURES / "ask.sse").read_text(encoding="utf-8")
# The fixture's last event is `done`, with the recorded answer's time and tokens.
ASK_DONE = json.loads(ASK_SSE.strip().split("\n\n")[-1].split("data: ", 1)[1])
ASK_WINDOW = json.loads((FIXTURES / "ask_request.json").read_text(encoding="utf-8"))["window_id"]


@pytest.fixture
def base_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """The fake-mode app served by uvicorn on a free local port, as the scripts reach it."""
    monkeypatch.setattr(fake, "EVENT_DELAY_S", (0.0, 0.0))
    app = create_app(Settings(engine="fake", model=DEFAULT_MODEL))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(10)


def lines(text: str) -> list[bytes]:
    return [line.encode("utf-8") for line in text.splitlines(keepends=True)]


class Clock:
    """Advances 0.1 s on every reading."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        self.now += 0.1
        return self.now


def test_parse_sse_reads_the_fixture_and_skips_pings() -> None:
    messages = list(parse_sse(lines(": ping\n\n" + ASK_SSE)))

    assert [event for event, _ in messages] == ["tool_call", "tool_result", "answer", "done"]
    assert messages[-1] == ("done", ASK_DONE)


def test_read_stream_records_timings_steps_and_tokens() -> None:
    record = read_stream(lines(ASK_SSE), started=0.0, clock=Clock())

    assert record["first_event_ms"] == 100.0
    assert record["done_ms"] == 500.0
    assert record["tool_steps"] == 1
    assert record["completion_tokens"] == ASK_DONE["tokens"]
    assert (record["answer"], record["answer_source"]) == ("DIAGNOSIS_CLOSED", "tool")
    assert record["error"] is None
    assert [e["event"] for e in record["events"]] == ["tool_call", "tool_result", "answer", "done"]


def test_read_stream_keeps_the_error_code() -> None:
    error = {"error": {"code": "timeout", "message": "The answer took longer than 60 s."}}
    stream = f"event: tool_call\ndata: {{}}\n\nevent: error\ndata: {json.dumps(error)}\n\n"

    record = read_stream(lines(stream), started=0.0, clock=Clock())

    assert record["error"] == "timeout"
    assert record["completion_tokens"] is None


def test_read_stream_without_answer_is_an_error() -> None:
    assert read_stream(lines(": ping\n\n"), started=0.0, clock=Clock())["error"] == "no_answer"


def test_percentiles_interpolate_like_numpy() -> None:
    values = [float(v) for v in range(1, 11)]

    assert percentile(values, 50) == 5.5
    assert percentile(values, 90) == 9.1
    assert percentile([], 50) is None
    assert summarize([3.0, None, 1.0]) == {"n": 2, "p50": 2.0, "p90": 2.8, "max": 3.0}


def test_ask_once_reads_the_fake_stream(base_url: str) -> None:
    record = ask_once(ApiClient(base_url), "What went wrong in this record?", ASK_WINDOW)

    assert record["status"] == 200
    assert record["error"] is None
    assert record["answer"] == "DIAGNOSIS_CLOSED"
    assert (record["tool_steps"], record["completion_tokens"]) == (1, ASK_DONE["tokens"])
    assert 0 < record["first_event_ms"] <= record["done_ms"]


def test_ask_once_records_an_http_error(base_url: str) -> None:
    record = ask_once(ApiClient(base_url), "What went wrong?", "W-1")

    assert (record["status"], record["error"]) == (422, "bad_request")


def test_main_measures_every_level(base_url: str, tmp_path: Path) -> None:
    out = tmp_path / "out" / "measure.json"

    code = main(["--base-url", base_url, "--out", str(out), "--levels", "1,2", "--requests", "3"])

    result = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0
    assert result["measured_at"].endswith("+00:00")
    assert result["health"]["engine"] == "fake"
    assert result["system"] == json.loads((FIXTURES / "system.json").read_text("utf-8"))
    assert [level["concurrency"] for level in result["levels"]] == [1, 2]
    for level in result["levels"]:
        assert (level["requests"], level["errors"]) == (3, 0)
        assert len(level["measurements"]) == 3
        assert level["first_event_ms"]["n"] == 3
        assert level["first_event_ms"]["p50"] <= level["done_ms"]["max"]
        assert level["completion_tokens"]["max"] == ASK_DONE["tokens"]
    assert result["night"] is None


def test_during_night_stops_when_the_night_never_scans(base_url: str, tmp_path: Path) -> None:
    # Fake mode reports every run as the finished stored night, so no scan step is ever seen.
    out = tmp_path / "measure.json"

    code = main(["--base-url", base_url, "--out", str(out), "--during-night"])

    night = json.loads(out.read_text(encoding="utf-8"))["night"]
    assert code == 1
    assert night["run_id"] == "run-20261015-1"
    assert night["max_windows"] == 300
    assert night["error"] == "the night finished before its scan step"


class StubClient(ApiClient):
    def __init__(self, progress: list[dict[str, Any]]) -> None:
        super().__init__("http://stub")
        self.progress = progress
        self.calls = 0

    def json(self, method: str, path: str, body: Any = None) -> Any:
        self.calls += 1
        return self.progress[min(self.calls, len(self.progress)) - 1]


def test_wait_for_run_stops_at_the_step() -> None:
    client = StubClient(
        [
            {"status": "queued", "step": None},
            {"status": "running", "step": "facts"},
            {"status": "running", "step": "scan"},
            {"status": "running", "step": "report"},
        ]
    )

    progress = wait_for_run(client, "run-1", lambda p: p["step"] == "scan", 5, poll_s=0)

    assert progress["step"] == "scan"
    assert client.calls == 3


def test_wait_for_run_times_out() -> None:
    client = StubClient([{"status": "running", "step": "gate"}])

    with pytest.raises(TimeoutError):
        wait_for_run(client, "run-1", lambda _: False, 0, poll_s=0)


def test_note_counts_first_attempt_and_final(tmp_path: Path) -> None:
    db = NightDb(tmp_path / "night.sqlite").init()
    db.create_run(RunRecord(run_id="run-1", status="done", source="live"))
    db.put_windows("run-1", [window(f"W-00000{i}", i) for i in range(4)])
    db.put_notes(
        "run-1",
        [
            note("W-000000", 1, True),
            note("W-000001", 1, False),
            note("W-000001", 2, True),
            note("W-000002", 1, False),
            note("W-000002", 2, False),
        ],
    )

    counts = note_counts(db.path, "run-1")

    assert counts == {"windows": 3, "passed_first_attempt": 1, "passed_final": 2, "retried": 2}
    assert note_counts(db.path, "run-2") == {
        "windows": 0,
        "passed_first_attempt": 0,
        "passed_final": 0,
        "retried": 0,
    }
    assert note_counts(tmp_path / "missing.sqlite", "run-1") is None
    assert not (tmp_path / "missing.sqlite").exists()
