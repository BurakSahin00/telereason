# Fixtures

Example API responses generated from the 11 Oct 2026 run on one AMD Instinct MI300X.
Do not edit by hand; regenerate from `backend/`:

```bash
uv run python scripts/make_fixtures.py --results <results dir> --out ../fixtures
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
| `night_run.json` | `GET /api/night-runs/{run_id}` (the stored 11 Oct night) |
| pending (#28) | `GET /api/windows/{window_id}/signals` (needs raw time series) |

`window_detail.json` holds 5 windows: 2 flagged AURSAD, 1 unflagged AURSAD, 2 FactoryWave.

## Where the values come from

- Timings, window counts, flagged count, model: `p35_gece.json` (measured night run).
- Verdicts (`DAGITMA` = `closed`, `KOSULLU` = `screening`, `GECER` = `open`), AUROC and its
  interval, XGBoost version: `kapi_gpu.json`.
- Windows, facts, look-first signal, notes: `gece_notlar.jsonl`. The note is the last attempt
  with markdown marks removed; `verified` is whether that attempt passed the verifier. Window ids
  that occur more than once in the run are skipped.
- `plain`: |z| < 2 within the normal range, 2 to 3 slightly off normal, 3 or more clearly off
  normal (to be aligned with #16). `top_features` is empty until #45.
- AURSAD `threshold` 0.9: from `.claude/rules/claims.md`; the script stops unless it
  flags exactly the run's flagged count.
- `memory_total_gib`: `p35_gece.json`. `memory_used_gib`: card peak in the load test while the
  engine was loaded (`p34_yuk.json`, phase C), MiB / 1024.
- Version strings other than `xgboost`, labels, explanations and example questions:
  `docs/api.md`.
- `ask.sse`: target behaviour on a FactoryWave window (diagnosis closed). `total_ms` is the
  single-question p90 alone on the card (`p35_amd.json`); `tokens` is the mean tokens per
  question with one user (`kapasite_k1.json`); `ms` is `null` because tool time was not measured.

Contains small samples derived from FactoryBench (CC BY 4.0); see `DATA_NOTICE.md`.
