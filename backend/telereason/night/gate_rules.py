"""Rules v0 of the gate: which claims the data supports on a line, decided by code.

The rules were written on 10 Oct before the measured runs and were not changed afterwards.
Per line, from out-of-fold predictions:

- ANOMALY: 1 - P(normal) separates normal from faulty windows.  Open when the AUROC over all
  test windows is at least 0.80, the lower end of its group-bootstrap interval at least 0.70,
  and the within-group AUROC (only normal-faulty pairs from the same group) at least 0.75.
- CAUSE: on faulty windows, how often the true class is in the model's top 3 (normal left out)
  minus how often it is in the top 3 of a prior.  The prior ranks classes by count in the
  fold's training windows of the same task on FactoryWave, over all training windows on AURSAD.
  Open when the difference is at least +10 points and its group-bootstrap lower end above 0.
- K6: the AUROC with shuffled labels (same folds, one seed) is lower than the real one in all
  19 shuffles (p <= 0.05).
- K7: fold 0, seed 0 trained twice gives the same top-1 class for every test window.
- K1 (information): Cramer's V between label and group, and classes seen in one group only.

Decision, in this order: unmeasurable (fewer than 3 groups); do not deploy (K6 or K7 fails, or
neither ANOMALY nor CAUSE is open); pass (both open); conditional (only one open).  The API
shows do not deploy and unmeasurable as ``closed``, conditional as ``screening`` and pass as
``open``.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import numpy as np

from telereason.api.schemas import Verdict
from telereason.night.folds import LineSetup

Decision = Literal["unmeasurable", "do_not_deploy", "pass", "conditional"]
VERDICTS: dict[Decision, Verdict] = {
    "unmeasurable": "closed",
    "do_not_deploy": "closed",
    "conditional": "screening",
    "pass": "open",
}
BOOTSTRAP = 2000
BOOTSTRAP_SEED = 25
SHUFFLE_SEED = 1000


@dataclass(frozen=True)
class Rules:
    version: str = "v0"
    anomaly_auroc: float = 0.80
    anomaly_lower: float = 0.70
    anomaly_within_group: float = 0.75
    cause_difference: float = 10.0
    cause_lower: float = 0.0
    k6_permutations: int = 19
    k6_p: float = 0.05
    k7_changed: int = 0
    min_groups: int = 3


RULES_V0 = Rules()


def decide(
    n_groups: int,
    anomaly_open: bool,
    cause_open: bool,
    k6_passed: bool,
    k7_passed: bool,
    rules: Rules = RULES_V0,
) -> Decision:
    if n_groups < rules.min_groups:
        return "unmeasurable"
    if not k6_passed or not k7_passed or (not anomaly_open and not cause_open):
        return "do_not_deploy"
    if anomaly_open and cause_open:
        return "pass"
    return "conditional"


# ---------------------------------------------------------------- statistics


def auc(y: Sequence[int], score: Sequence[float]) -> float | None:
    """AUROC, or None when only one class is present."""
    from sklearn.metrics import roc_auc_score

    labels = np.asarray(y)
    if not len(labels) or labels.min() == labels.max():
        return None
    return float(roc_auc_score(labels, score))


def within_group_auc(
    y: Sequence[int], score: Sequence[float], groups: Sequence[str]
) -> tuple[float | None, int]:
    """AUROC over (faulty, normal) pairs from the same group, and the number of such pairs."""
    members: dict[str, list[int]] = defaultdict(list)
    for i, group in enumerate(groups):
        members[group].append(i)
    total = weight = 0.0
    for index in members.values():
        labels = np.asarray([y[i] for i in index])
        positive = int(labels.sum())
        negative = len(labels) - positive
        if positive == 0 or negative == 0:
            continue
        value = auc(labels, [score[i] for i in index])
        total += (value or 0.0) * positive * negative
        weight += positive * negative
    return (total / weight if weight else None), int(weight)


def group_bootstrap(
    groups: Sequence[str],
    statistic: Callable[[list[int]], float | None],
    n: int = BOOTSTRAP,
    seed: int = BOOTSTRAP_SEED,
) -> tuple[float | None, float | None]:
    """95 % interval of ``statistic`` over groups drawn with replacement (percentile method)."""
    members: dict[str, list[int]] = defaultdict(list)
    for i, group in enumerate(groups):
        members[group].append(i)
    keys = sorted(members)
    if len(keys) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(n):
        drawn = rng.integers(0, len(keys), size=len(keys))
        value = statistic([i for k in drawn for i in members[keys[k]]])
        if value is not None:
            values.append(value)
    if len(values) < n // 2:
        return None, None
    values.sort()
    return values[int(0.025 * len(values))], values[int(0.975 * len(values)) - 1]


def group_bootstrap_mean(
    values: Sequence[float], groups: Sequence[str], n: int = BOOTSTRAP, seed: int = BOOTSTRAP_SEED
) -> tuple[float | None, float | None]:
    """95 % interval of the mean, in percentage points, over groups drawn with replacement."""
    sums: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    for value, group in zip(values, groups, strict=True):
        sums[group][0] += value
        sums[group][1] += 1
    keys = sorted(sums)
    if len(keys) < 2:
        return None, None
    total = np.array([sums[k][0] for k in keys])
    count = np.array([sums[k][1] for k in keys])
    rng = np.random.default_rng(seed)
    means: list[float] = []
    batch = max(1, min(n, 2_000_000 // len(keys)))
    while len(means) < n:
        size = min(batch, n - len(means))
        drawn = rng.integers(0, len(keys), size=(size, len(keys)))
        means.extend((100 * total[drawn].sum(1) / count[drawn].sum(1)).tolist())
    means.sort()
    return means[int(0.025 * n)], means[int(0.975 * n) - 1]


def cramer_v(a: Sequence[Any], b: Sequence[Any]) -> float | None:
    rows, columns = sorted(set(a)), sorted(set(b))
    if len(rows) < 2 or len(columns) < 2:
        return None
    row_of = {x: i for i, x in enumerate(rows)}
    column_of = {x: i for i, x in enumerate(columns)}
    table = np.zeros((len(rows), len(columns)))
    for x, z in zip(a, b, strict=True):
        table[row_of[x], column_of[z]] += 1
    n = table.sum()
    expected = table.sum(1, keepdims=True) @ table.sum(0, keepdims=True) / n
    chi2 = float(((table - expected) ** 2 / np.where(expected > 0, expected, 1)).sum())
    return float(np.sqrt(chi2 / (n * (min(table.shape) - 1))))


def shuffle_labels(labels: Sequence[int], pool: Sequence[int], shuffle: int) -> np.ndarray:
    """Labels with the line's pool permuted; shuffle ``r`` uses seed 1000 + r."""
    rng = np.random.default_rng(SHUFFLE_SEED + shuffle)
    out = np.array(labels)
    index = np.asarray(pool)
    out[index] = out[index][rng.permutation(len(index))]
    return out


# ---------------------------------------------------------------- one line


@dataclass
class Predictions:
    """Out-of-fold class probabilities (test windows x classes) of one line."""

    real: dict[int, list[np.ndarray]]  # fold -> one matrix per seed, seeds in order
    repeat: dict[int, np.ndarray]  # K7: fold 0, seed 0 trained again
    shuffled: dict[int, dict[int, np.ndarray]]  # shuffle -> fold -> matrix


@dataclass(frozen=True)
class WindowScore:
    index: int
    fold: int
    p_normal: float


def _ranking(counts: Counter[int], overall: Counter[int], n_classes: int) -> list[int]:
    return sorted(range(n_classes), key=lambda c: (-counts.get(c, 0), -overall.get(c, 0), c))


def evaluate(
    setup: LineSetup,
    predictions: Predictions,
    labels: Sequence[int],
    tasks: Sequence[str],
    classes: Sequence[str],
    normal: int,
    rules: Rules = RULES_V0,
) -> tuple[dict[str, Any], list[WindowScore]]:
    """The line's checks and decision, and the out-of-fold P(normal) of every test window."""
    n_classes = len(classes)
    rows = []
    for k, fold in enumerate(setup.folds):
        if k not in predictions.real:
            continue
        p = np.mean(predictions.real[k], axis=0)
        seen = {labels[i] for i in fold.fit}
        mask = np.array([c in seen for c in range(n_classes)])
        overall = Counter(labels[i] for i in fold.fit)
        by_task: dict[str, Counter[int]] = defaultdict(Counter)
        for i in fold.fit:
            by_task[tasks[i]][labels[i]] += 1
        for j, i in enumerate(fold.test):
            ranked = [int(c) for c in np.argsort(-np.where(mask, p[j], -1.0))]
            use_task = setup.prior == "task" and tasks[i] in by_task
            prior = _ranking(by_task[tasks[i]] if use_task else overall, overall, n_classes)
            rows.append(
                {
                    "i": i,
                    "label": labels[i],
                    "group": setup.groups[i],
                    "p_normal": float(p[j, normal]),
                    "model_top3": [c for c in ranked if c != normal][:3],
                    "prior_top3": [c for c in prior if c != normal][:3],
                    "fold": k,
                }
            )

    y = [int(r["label"] != normal) for r in rows]
    score = [1 - r["p_normal"] for r in rows]
    groups = [r["group"] for r in rows]
    anomaly_auc = auc(y, score)
    lower, upper = group_bootstrap(
        groups, lambda ix: auc([y[i] for i in ix], [score[i] for i in ix])
    )
    within, pairs = within_group_auc(y, score, groups)
    anomaly_open = bool(
        anomaly_auc is not None
        and anomaly_auc >= rules.anomaly_auroc
        and lower is not None
        and lower >= rules.anomaly_lower
        and within is not None
        and within >= rules.anomaly_within_group
    )

    faulty = [r for r in rows if r["label"] != normal]
    hits = [
        float(r["label"] in r["model_top3"]) - float(r["label"] in r["prior_top3"]) for r in faulty
    ]
    difference = 100 * statistics.fmean(hits) if hits else None
    d_lower, d_upper = (
        group_bootstrap_mean(hits, [r["group"] for r in faulty]) if hits else (None, None)
    )
    cause_open = bool(
        difference is not None
        and difference >= rules.cause_difference
        and d_lower is not None
        and d_lower > rules.cause_lower
    )

    shuffled_auc: list[float | None] = []
    for shuffle, by_fold in sorted(predictions.shuffled.items()):
        shuffled = shuffle_labels(labels, setup.pool, shuffle)
        yy: list[int] = []
        ss: list[float] = []
        for k, fold in enumerate(setup.folds):
            if k not in by_fold:
                continue
            for j, i in enumerate(fold.test):
                yy.append(int(shuffled[i] != normal))
                ss.append(1 - float(by_fold[k][j, normal]))
        shuffled_auc.append(auc(yy, ss))
    valid = [x for x in shuffled_auc if x is not None]
    k6_p = (
        (1 + sum(1 for x in valid if x >= anomaly_auc)) / (1 + len(valid))
        if valid and anomaly_auc is not None
        else None
    )
    k6_passed = bool(
        k6_p is not None and len(valid) >= rules.k6_permutations and k6_p <= rules.k6_p
    )

    changed = None
    if 0 in predictions.repeat and predictions.real.get(0):
        first = predictions.real[0][0].argmax(1)
        changed = int((first != predictions.repeat[0].argmax(1)).sum())
    k7_passed = changed is not None and changed <= rules.k7_changed

    line_groups = [setup.groups[i] for i in setup.pool]
    n_groups = len(set(line_groups))
    decision = decide(n_groups, anomaly_open, cause_open, k6_passed, k7_passed, rules)
    pool_labels = [labels[i] for i in setup.pool]
    groups_of_class: dict[int, set[str]] = defaultdict(set)
    for i in setup.pool:
        groups_of_class[labels[i]].add(setup.groups[i])
    single_group = sorted(
        classes[c] for c, g in groups_of_class.items() if len(g) == 1 and c != normal
    )

    result = {
        "decision": decision,
        "verdict": VERDICTS[decision],
        "n_test": len(rows),
        "anomaly": {
            "open": anomaly_open,
            "auroc": anomaly_auc,
            "interval": [lower, upper],
            "within_group": within,
            "within_group_pairs": pairs,
        },
        "cause": {
            "open": cause_open,
            "difference": difference,
            "interval": [d_lower, d_upper],
            "n": len(hits),
        },
        "k6": {"passed": k6_passed, "p": k6_p, "shuffled_auroc": shuffled_auc},
        "k7": {"passed": k7_passed, "changed": changed},
        "k1": {
            "cramer_v": cramer_v(pool_labels, line_groups),
            "single_group_classes": single_group,
            "groups": n_groups,
        },
    }
    scores = [WindowScore(index=r["i"], fold=r["fold"], p_normal=r["p_normal"]) for r in rows]
    return result, scores
