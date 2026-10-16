# TeleReason API (v0)

Contract between the backend (FastAPI) and the frontend. Until the real endpoints are ready, the backend's fake mode (`ENGINE=fake`) serves `fixtures/` as the responses, so the frontend always talks to the same backend; `GET /api/health` reports the real engine setting, not a fixture.

In real mode (`ENGINE=vllm`) with `NIGHT_SOURCE=stored`, for now the default and only value, `/api/report/latest`, `/api/gate`, `/api/examples`, `/api/windows` and `/api/windows/{window_id}` serve the stored 11 Oct night from `fixtures/`, and the live assistant's tools read the same files; `/api/windows/{window_id}/signals` reads the downloaded dataset; `/api/system` reads the GPU from `rocm-smi` and the installed versions, with the stored night's timings as `last_night`; `/api/night-runs` answers `501` until it is built.

Status: **draft v0**. Values in the examples are illustrative; `fixtures/` holds responses generated from the real 11 Oct MI300X run (see `fixtures/README.md`).

## Conventions

- Base path: `/api`. JSON everywhere except `POST /api/ask`, which streams Server-Sent Events.
- Field names are English `snake_case`. Durations end in `_s` (seconds) or `_ms` (milliseconds).
- Gate verdicts: `closed` (no diagnosis, facts only), `screening` (abnormal-behaviour score only, no cause), `open` (diagnosis allowed).
- Lines: `FactoryWave`, `AURSAD`.
- Window ids are strings and unique across the API.
- One error shape for every endpoint:

```json
{"error": {"code": "not_found", "message": "Window W-574006 does not exist."}}
```

| HTTP | `code` | When |
|---|---|---|
| 400 / 422 | `bad_request` | Invalid parameters or body |
| 404 | `not_found` | Unknown window or run id |
| 409 | `busy` | A night run is already active |
| 429 | `rate_limited` | Too many requests to `/api/ask` or `POST /api/night-runs` |
| 503 | `engine_not_ready` | The model engine is still loading |
| 501 | `not_implemented` | Endpoint stub not built yet (development only; none remain at v1.0) |
| – | `timeout` | `error` event only: a `/api/ask` answer took longer than 60 s |
| – | `internal_error` | `error` event only: `/api/ask` failed after the stream started |

A change to any schema goes in one PR together with the matching `fixtures/` files and carries the `api-change` label.

The Pydantic models are in `backend/telereason/api/schemas.py`. `docs/openapi.json` is generated from them (in `backend/`: `uv run python -m telereason.api.export_openapi`), and a test fails when it is out of date. Frontend types are generated from it.

## Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | Engine ready?, model name |
| GET | `/api/system` | GPU, memory, software versions, last night's timings |
| GET | `/api/gate` | Gate verdict per line |
| GET | `/api/report/latest` | Latest morning report |
| GET | `/api/windows` | Paged window list |
| GET | `/api/windows/{window_id}` | Facts, score and note for one window |
| GET | `/api/windows/{window_id}/signals` | Time series and normal band for the chart |
| POST | `/api/ask` | Live assistant, streamed (SSE) |
| GET | `/api/examples` | Example questions (Turkish and English) |
| POST | `/api/night-runs` | Start a small night run |
| GET | `/api/night-runs/{run_id}` | Progress of a night run |

### GET /api/health

```json
{"status": "ok", "engine": "vllm", "model": "openai/gpt-oss-120b", "ready": true}
```

`engine` is `vllm` or `fake`. While the model loads: `ready: false`.

### GET /api/system

```json
{
  "gpu": {"name": "AMD Instinct MI300X", "memory_total_gib": 191.6875, "memory_used_gib": 160.11891174316406},
  "versions": {"rocm": "7.2.3", "vllm": "0.27.1", "xgboost": "3.2.0", "python": "3.12"},
  "last_night": {"run_id": "2026-10-11-amd", "timings_s": {"gate": 462, "facts": 82, "scan": 1020, "report": 0, "total": 1566}}
}
```

Memory is in GiB (2^30 bytes), as the GPU driver reports it. `gpu` is `null` when `rocm-smi` is missing, fails, takes longer than 2 s or reports no readable card; fake mode serves `fixtures/system.json`. A `versions` entry other than `python` is `null` when that package is not installed. `last_night` is `null` before the first stored night run.

### GET /api/gate

```json
{
  "run_id": "2026-10-11-amd",
  "lines": [
    {
      "line": "FactoryWave",
      "verdict": "closed",
      "label": "Facts, not diagnosis",
      "auroc": 0.53,
      "auroc_ci": [0.0, 0.0],
      "explanation": "On new days, fault prediction is no better than chance, so this line gets facts only."
    },
    {
      "line": "AURSAD",
      "verdict": "screening",
      "label": "Abnormal-behaviour screening only",
      "auroc": 0.91,
      "auroc_ci": [0.0, 0.0],
      "explanation": "Normal and faulty runs separate well, but the fault cause is not reliable."
    }
  ]
}
```

`auroc_ci` is the interval from the gate output; the zeros above are placeholders.

### GET /api/report/latest

```json
{
  "run_id": "2026-10-11-amd",
  "timings_s": {"gate": 462, "facts": 82, "scan": 1020, "report": 0, "total": 1566},
  "lines": [
    {
      "line": "AURSAD",
      "verdict": "screening",
      "label": "Abnormal-behaviour screening only",
      "scanned": 4068,
      "flagged": 1879,
      "threshold": 0.9,
      "items": [
        {"window_id": "W-574006", "task": "aursad", "score": 1.0, "look_first": "feedback_tcp_4",
         "note": "The abnormal-behaviour score is 1.0. ...", "verified": true}
      ]
    },
    {"line": "FactoryWave", "verdict": "closed", "label": "Facts, not diagnosis", "scanned": 3354, "flagged": null, "threshold": null, "items": []}
  ]
}
```

- AURSAD `items` are sorted by `score`, highest first. FactoryWave `items` are the windows with the largest deviations.
- `verified: false` means the note did not pass the verifier; the frontend then shows facts only, not the note.
- On a line without a score (`closed`), `score`, `flagged` and `threshold` are `null`. The same holds for `score` and `flagged` in `/api/windows` and `/api/windows/{window_id}`.

### GET /api/windows

Query: `line` (`FactoryWave` | `AURSAD`), `flagged` (`true` | `false`), `sort` (`score` | `deviation`), `page` (from 1), `page_size` (default 50, max 200).

```json
{
  "page": 1, "page_size": 50, "total": 4068,
  "items": [
    {"window_id": "W-574006", "line": "AURSAD", "task": "aursad", "score": 1.0, "flagged": true,
     "look_first": "feedback_tcp_4", "verified": true}
  ]
}
```

### GET /api/windows/{window_id}

```json
{
  "window_id": "W-574006",
  "line": "AURSAD",
  "task": "aursad",
  "verdict": "screening",
  "score": 1.0,
  "flagged": true,
  "look_first": "feedback_tcp_4",
  "facts": [
    {"signal": "feedback_tcp_4", "window_mean": 0.0, "normal_mean": 0.0, "difference": 0.0, "z": 0.0,
     "plain": "Clearly off normal (3.7 standard deviations)."}
  ],
  "top_features": [{"feature": "feedback_tcp_4", "contribution": 0.0}],
  "note": "The abnormal-behaviour score is 1.0. ...",
  "verified": true
}
```

- `plain` is the technician-language reading of `z`.
- `top_features` (AURSAD only) are the gate's largest feature contributions; empty list on other lines.
- On a `closed` line no field contains a fault name.

### GET /api/windows/{window_id}/signals

Query: `channels` (comma-separated; default: `look_first` plus the two next largest deviations).

```json
{
  "window_id": "W-574006",
  "t_s": [0.0, 0.01, 0.02],
  "channels": [
    {"name": "feedback_tcp_4", "values": [0.0, 0.0, 0.0], "normal_low": [0.0, 0.0, 0.0], "normal_high": [0.0, 0.0, 0.0]}
  ]
}
```

### POST /api/ask

Request:

```json
{"question": "What went wrong in this record?", "window_id": "W-574006"}
```

`window_id` is optional; when given it is `W-` followed by six digits (for example `W-574006`), otherwise the request gets 422 `bad_request`. Harmony special tokens (any `<|...|>` string) are removed from `question` before it reaches the model. The response is an SSE stream; the frontend reads it with `fetch` (EventSource cannot POST).

```
event: tool_call
data: {"turn": 1, "tool": "classify_fault", "args": {}}

event: tool_result
data: {"turn": 1, "tool": "classify_fault", "summary": "diagnosis closed on this line", "ms": 640}

event: answer
data: {"answer": "DIAGNOSIS_CLOSED", "answer_source": "tool", "text": "Diagnosis is closed on this line because ..."}

event: done
data: {"total_ms": 3400, "tokens": 812}
```

- An `error` event (`data` = the error shape above) ends the stream early, without `done`: `timeout` when no answer came within 60 s, `internal_error` when the assistant failed, `engine_not_ready` when the engine stopped mid-answer. Errors before the stream starts (for example `bad_request`, `rate_limited`, `engine_not_ready`) are plain JSON responses.
- Payload models in `/openapi.json`: `ToolCallEvent`, `ToolResultEvent`, `AnswerEvent`, `DoneEvent` and `ErrorResponse`. The 200 response describes one message as `{"event", "data"}`.
- A comment line (`: ping`) is sent every 15 s so proxies keep the stream open; clients ignore it.
- When the client disconnects, the server stops the answer and cancels the model request.
- `ms` is the tool's run time; `null` when it was not measured.
- `total_ms` is the time from the request to `done`; `tokens` is the number of tokens the model generated for this question (0 when the model did not run).
- `answer_source` says who wrote `text`: `tool` when code wrote it (from a tool result, a refusal or the not-answerable text), `model` when the language model wrote it.
- `answer` is one of these codes:

| `answer` | `answer_source` | When |
|---|---|---|
| `ANSWERED` | `model` | The model answered from tool results |
| `FACTS_ONLY` | `tool` | The model's answer named a fault on a `closed` or `screening` line; it is replaced by the verified facts |
| `DIAGNOSIS_CLOSED` | `tool` | Fault question on a `closed` line: the gate verdict and the verified facts, no cause |
| `SCREENING_ONLY` | `tool` | Fault question on a `screening` line: the abnormal-behaviour score, no cause |
| `ACTION_REFUSED` | `tool` | A request to start, stop or change equipment (for example "Stop the robot"); refused before the model runs |
| `NOT_ANSWERABLE` | `tool` | The tools cannot answer the question, or it is not about these robot cells |

### GET /api/examples

```json
{"examples": [
  {"lang": "en", "text": "What went wrong in this record?"},
  {"lang": "tr", "text": "Bu kayıtta ne ters gitti?"}
]}
```

### POST /api/night-runs

Request: `{"max_windows": 300}`. Response `202`:

```json
{"run_id": "run-20261015-1", "status": "queued"}
```

Only one run at a time: while one is active, the response is `409` with code `busy`.

### GET /api/night-runs/{run_id}

```json
{
  "run_id": "run-20261015-1",
  "status": "running",
  "step": "scan",
  "done": 120,
  "total": 300,
  "timings_s": {"gate": 31, "facts": 4, "scan": 95}
}
```

`status`: `queued` | `running` | `done` | `failed`. `step`: `gate` | `facts` | `scan` | `report`, `null` while queued. Every step appears in `timings_s`; a step that has not finished is `null`.