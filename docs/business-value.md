# Business value

On AURSAD, TeleReason screens for unusual behaviour; it does not name a cause. In the
16 Oct night the gate's XGBoost score separates normal and faulty windows with AUROC
0.909 (interval 0.898 to 0.920). At the gate threshold of `0.9` it flagged 68.8% of the
windows labelled faulty in the benchmark and 3.9% of the normal ones in the 11 Oct run.
These are rates within each class, measured on lab data. They are not a guarantee for a
production line.

## Per 1,000 windows

At the 11 Oct rates:

| Windows | Flagged | Reading |
| --- | ---: | --- |
| 1,000 labelled faulty | 688 | 68.8% of faulty windows flagged |
| 1,000 normal | 39 | 3.9% false alarms |

## What a technician reviews in a mixed stream

The number of flagged windows depends on how many windows are faulty. With `p` the
share of faulty windows, the expected number flagged per 1,000 windows is:

```text
688 × p + 39 × (1 − p)
```

In the AURSAD lab data about 65% of the windows are faulty (`p ≈ 0.65`), so about
461 of every 1,000 windows are flagged (46%). In the 16 Oct night 1,887 of the 4,068
AURSAD windows were flagged (46.4%, calculation from the two counts). A production line
has far fewer faulty windows; its `p` has to be measured on that line before the formula
gives its review load.

## Source

- AUROC, its interval and the flagged count of the 16 Oct night: `fixtures/gate.json` and
  `fixtures/report_latest.json`, generated from the store of night run `run-20261016-1` on
  the AMD Instinct MI300X.
- Rates within each class (68.8%, 3.9%): measured in the 11 Oct run on the AMD Instinct
  MI300X. The 16 Oct files do not carry them, because they need the window labels.
- Threshold `0.9`: `SCORE_THRESHOLD` in `backend/telereason/night/report.py`, shown in
  `fixtures/report_latest.json`.
