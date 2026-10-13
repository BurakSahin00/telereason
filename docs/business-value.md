# Business value

On AURSAD, TeleReason screens for unusual behaviour; it does not name a cause. At the
gate threshold of `0.9`, the gate's XGBoost score (AUROC 0.91) flags 68.8% of the
windows labelled faulty in the benchmark and 3.9% of the normal ones. These are
rates within each class, measured on lab data. They are not a guarantee for a
production line.

## Per 1,000 windows

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
461 of every 1,000 windows are flagged (46%). A production line has far fewer
faulty windows; its `p` has to be measured on that line before the formula gives
its review load.

## Source

Measured on the 11 Oct run on the AMD Instinct MI300X: gate model (XGBoost), threshold
`0.9` as set in `backend/scripts/make_fixtures.py` and shown in
`fixtures/report_latest.json`.
