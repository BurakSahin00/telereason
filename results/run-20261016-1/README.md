# Night run run-20261016-1

The first full night run by the app itself, with the files that back the numbers in the
main README and in `fixtures/` (generated from this run's store).

- Run: `run-20261016-1`, started 2026-10-16 22:22:57 UTC, finished 2026-10-16 22:49:12 UTC.
  The times in `startup.log` are UTC too.
- Hardware: one AMD Instinct MI300X on AMD Developer Cloud.
- Model: `openai/gpt-oss-120b` in vLLM (`startup.log`).
- Software: ROCm 7.2.3 and vLLM 0.27.1+rocm723 (`system.json`, `versions`); XGBoost 3.2.0
  (`gate.json`, `xgboost`; `system.json` lists it as `null`), trained on the GPU
  (`device_used`).
- Started as in [infra/droplet.md](../../infra/droplet.md), "Night runs and measurements":
  the app with `NIGHT_SOURCE=db` and `GATE_DEVICE=auto` (step 1), then
  `POST /api/night-runs` with `{"max_windows": 100000}` (step 2), so the gate ran and every
  window it tested was scanned. The smoke questions ran afterwards with
  `backend/scripts/smoke_ask.py` (section "Measurement scripts").

| File | What it is |
|---|---|
| `gate.json` | The gate's output for this night, copied as written: rules, XGBoost version and parameters, device, windows, trainings and seconds, and per line the verdict, the AUROC with its interval and the checks behind the verdict. It lists the gate's class labels, so it holds fault names; it is not served by the API. |
| `smoke.json` | `smoke_ask.py` output, copied as written: ten fixed questions (`backend/scripts/smoke_questions.json`) against the running app, each with its events, timings and checks, and a summary line. |
| `system.json` | The app's `GET /api/system` response on the droplet after the night, saved as returned: the software versions and the timings of this run. `gpu` is `null` because on the MI300X virtual function `rocm-smi` gives no product name, so the app cannot name the card; the fix is in a separate pull request. |
| `notes.json` | Note counts of this run, read from its store with `backend/scripts/notes_summary.py --db <store> --run run-20261016-1 --out notes.json` (the store opened read only): windows, windows with and without a note, note attempts, notes shown (last attempt passed the verifier), attempts that passed on the first and on the second try, and the mean completion tokens per attempt. Counts only, no note text. |
| `startup.log` | Four lines of the app log, nothing else: the night pool (items read and kept, and its load time, which includes the window facts) and the vLLM engine start and ready times. |

Not here: the window scores (`scores.jsonl`) and the SQLite store. The files read from the
store are `notes.json` (above) and `fixtures/`, generated with `backend/scripts/make_fixtures.py`.

## AURSAD rates within each class

The share of faulty and of normal AURSAD windows flagged at the threshold 0.9 cannot be
recomputed for this night from these files: neither the store's `windows` table nor
`scores.jsonl` carries a window label (normal or faulty). The rates in the main README and
`docs/business-value.md` (68.8% and 3.9%) stay those of the 11 Oct run.
