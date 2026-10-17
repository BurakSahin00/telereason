# Droplet session of 17 Oct 2026

Live-answer measurements and a smoke test of the assistant on one AMD Instinct MI300X
(AMD Developer Cloud), with the files that back the latency tables in the main README.

- Date: 2026-10-17. The `measured_at` times in the files are UTC: `smoke.json` 11:38:27,
  `measure.json` 12:05:46, `measure_night.json` 12:06:21.
- Served run: `run-20261016-1` (16 Oct, the full night described in
  [results/run-20261016-1/](../run-20261016-1/)), served from the store. It is the
  `last_night` in every `system` snapshot here.
- Small night: `run-20261017-1`, 300 windows, started by `measure_ask.py --during-night`
  while it measured. When it finished, the app served it as the latest run.
- Model: `openai/gpt-oss-120b` in vLLM (`health` in each file).
- Started as in [infra/droplet.md](../../infra/droplet.md), "Night runs and measurements":
  the app with `NIGHT_SOURCE=db`, `GATE_DEVICE=auto` and `RATE_LIMIT_ASK_PER_MIN=0` (step 1).
  The scripts ran inside the container against `http://127.0.0.1:8000` (section
  "Measurement scripts").

| File | Made with | What it is |
|---|---|---|
| `system.json` | `docker exec rocm curl -s 127.0.0.1:8000/api/system > /root/bench/system.json` on the droplet host: the app's `GET /api/system` response, saved as returned. | The GPU (`"AMD Instinct MI300X VF"`, total and used memory in GiB), the software versions (ROCm 7.2.3, vLLM 0.27.1+rocm723, XGBoost 3.2.0, Python 3.12) and `last_night`, the timings of `run-20261016-1`. `fixtures/system.json` takes `gpu` and `versions` from this file. |
| `smoke.json` | `smoke_ask.py --out /root/bench/smoke.json --data-dir /root/data --gate-json /root/data/runs/<run_id>/gate.json`, with the gate of `run-20261016-1` | Ten fixed questions (`backend/scripts/smoke_questions.json`) against the running app, each with its events, timings and checks, and the summary line: 10/10 passed; stream 10/10, from tools 8/8, action refused 2/2, no fault name 10/10. `terms` holds container paths and counts only, no fault names. Not a latency measurement. |
| `measure.json` | `measure_ask.py --out /root/bench/measure.json --levels 1,4,8 --requests 8` | Live-question latency with no night running (`settings.during_night` is `false`, `night` is `null`): at concurrency 1, 4 and 8, 8 questions each, three questions sent in turn. Per level the errors, wall time and p50, p90 and max of `first_event_ms`, `done_ms`, tool steps and completion tokens, then the raw measurements with each answer. |
| `measure_night.json` | `measure_ask.py --out /root/bench/measure_night.json --during-night --max-windows 300 --db /root/data/telereason.sqlite` | The same levels while the small night `run-20261017-1` ran. Each level also records `night_step`, the night's step before and after it: level 1 `scan` → `scan`, level 4 `scan` → `report`, level 8 `report` → `report`. `night` holds the run id, status, step timings and the note counts from the store. |

## What to keep in mind when reading these files

- Only level 1 of `measure_night.json` ran entirely within the scan step.
- After the small night finished, the app served `run-20261017-1`. The two windows the
  questions ask about (`W-574006`, `W-246062`) are not in it, so 2 of the 8 answers at
  level 4 and 5 of the 8 at level 8 of `measure_night.json` are `NOT_ANSWERABLE`. Every
  other answer in `measure.json` and `measure_night.json` is `ANSWERED`, and no question
  ended in an error.
- The small night's gate step took under 0.1 s because it trained no gate: a night smaller than
  the gate's test set reuses the latest stored gate, here the one of `run-20261016-1`.
  `night.notes`: 300 of 300 notes passed the verifier on the first attempt, none was retried.

Not here: the SQLite store, the scores and the app log.
