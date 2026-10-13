# Business value

TeleReason's strongest measured result is reported at the AURSAD gate threshold
of `0.9`: it catches `68.8%` of faulty windows and flags `3.9%` of normal
windows. These are conditional rates, not a claim about a mixed production
stream. The source measurement and its caveat are tracked in [issue #50](https://github.com/BurakSahin00/telereason/issues/50).

## Technician translation

Per 1,000 windows of a known class:

| Population | Windows reviewed/flagged | Interpretation |
| --- | ---: | --- |
| 1,000 faulty windows | 688 caught | 68.8% detection rate |
| 1,000 normal windows | 39 flagged | 3.9% false-alarm rate |

The number of windows a technician reviews in a mixed stream depends on the
fault prevalence. If `p` is the proportion of faulty windows, the expected
flagged volume per 1,000 windows is:

```text
688 × p + 39 × (1 - p)
```

For example, the calculation must use a measured value of `p`; it must not
assume that half of all windows are faulty. These measurements come from the
lab/benchmark run and should not be presented as a field-production guarantee.

The gate threshold is defined as `0.9` in
`backend/scripts/make_fixtures.py` and appears in the generated report fixture
at `fixtures/report_latest.json`.
