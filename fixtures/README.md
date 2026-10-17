# Fixtures

Example API responses of one night run, read from the SQLite store of night runs.

- Run: `run-20261016-1`
- Date: 2026-10-16
- Source: store `telereason.sqlite`; the run was run by the app (`POST /api/night-runs`)

Do not edit by hand; regenerate from `backend/`:

```bash
uv run python -m scripts.make_fixtures --db <DB_PATH> --run run-20261016-1 --out ../fixtures
```

`backend/tests/test_fixtures.py` validates every file against the models in
`backend/telereason/api/schemas.py`.

| File | Endpoint |
|---|---|
| `health.json` | `GET /api/health` |
| `system.json` | `GET /api/system` |
| `gate.json` | `GET /api/gate` |
| `report_latest.json` | `GET /api/report/latest` |
| `windows.json` | `GET /api/windows?page=1&page_size=20` (no filter, night order) |
| `window_detail.json` | `GET /api/windows/{window_id}`: object keyed by window id |
| `ask_request.json` | `POST /api/ask` request body |
| `ask.sse` | `POST /api/ask` response stream |
| `examples.json` | `GET /api/examples` |
| `night_run_started.json` | `POST /api/night-runs` (202) |
| `night_run.json` | `GET /api/night-runs/{run_id}` (run `run-20261016-1`) |
| pending (#28) | `GET /api/windows/{window_id}/signals` (needs raw time series) |

`window_detail.json` holds 21 windows: every window listed in `report_latest.json`
and the `ask_request.json` window, plus one unflagged AURSAD window.
By line: 10 flagged AURSAD, 1 unflagged AURSAD, 10 FactoryWave.
`windows.json` is one page, so most of these windows are not in it; in fake mode
`GET /api/windows/{window_id}` reads `window_detail.json` first.

## Where the values come from

- Run `run-20261016-1` in the store, read the way the API reads it with `NIGHT_SOURCE=db`
  (`telereason.store.night`): `report_latest.json`, `gate.json`, `windows.json`,
  `window_detail.json`, `night_run.json`, `last_night` in `system.json` and the window of
  `ask_request.json` (the closed-line window the recorded `ask.sse` asks about).
- Window ids are the store's, so each is unique. A note is shown only when it passed the
  verifier; otherwise `note` is empty and `verified` is false. `top_features` is shown on AURSAD
  only and is empty when the run stored none.
- `plain`: |z| < 2 within the normal range, 2 to 3 slightly off normal, 3 or more clearly off
  normal.
- `health.json`, `examples.json` and `night_run_started.json`: kept from the previous fixtures,
  because a night run does not store them. Example questions: `docs/api.md`.
- `gpu` and `versions` in `system.json`: saved `GET /api/system` response `system.json` (run `run-20261016-1`, file time 2026-10-17 12:16 UTC): the GPU as `rocm-smi` reported it and the installed versions.
- `ask.sse`: recorded `POST /api/ask` stream `ask.sse` for `ask_request.json` (run `run-20261016-1`, file time 2026-10-16 23:07 UTC): one real answer, so `total_ms`, `tokens` and `ms` are that answer's.
- The first fixtures took the kept values from the 11 Oct MI300X run: the model, the gate's
  XGBoost version, `memory_total_gib` (the card's memory), `memory_used_gib` (card peak in the
  load test while the engine was loaded, MiB / 1024; vLLM reserves 85% of GPU memory for its
  cache, so this is not what the model needs), `total_ms` (single-question p90 alone on
  the card) and `tokens` (mean tokens per question with one user); `ms` was `null` because tool
  time was not measured. Their other version strings: `docs/api.md`.

Contains small samples derived from FactoryBench (license: see `DATA_NOTICE.md`).
