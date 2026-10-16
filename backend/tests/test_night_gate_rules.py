import numpy as np
import pytest

from telereason.night.folds import Fold, LineSetup
from telereason.night.gate_rules import (
    VERDICTS,
    Predictions,
    auc,
    cramer_v,
    decide,
    evaluate,
    group_bootstrap,
    group_bootstrap_mean,
    shuffle_labels,
    within_group_auc,
)

# Decision order


@pytest.mark.parametrize(
    ("args", "decision"),
    [
        ((2, True, True, True, True), "unmeasurable"),
        ((3, False, False, True, True), "do_not_deploy"),
        ((3, True, True, False, True), "do_not_deploy"),  # K6 fails
        ((3, True, True, True, False), "do_not_deploy"),  # K7 fails
        ((3, True, True, True, True), "pass"),
        ((3, True, False, True, True), "conditional"),
        ((3, False, True, True, True), "conditional"),
    ],
)
def test_decision_order(args: tuple, decision: str) -> None:
    assert decide(*args) == decision


def test_verdicts_follow_the_api_mapping() -> None:
    assert VERDICTS == {
        "unmeasurable": "closed",
        "do_not_deploy": "closed",
        "conditional": "screening",
        "pass": "open",
    }


# Statistics


def test_auc_needs_both_classes() -> None:
    assert auc([0, 0], [0.1, 0.2]) is None
    assert auc([0, 1], [0.1, 0.9]) == 1.0


def test_within_group_auc_uses_only_pairs_from_the_same_group() -> None:
    y = [0, 1, 0, 1]
    score = [0.1, 0.9, 0.95, 0.99]
    groups = ["a", "a", "b", "b"]

    value, pairs = within_group_auc(y, score, groups)

    assert value == 1.0  # across groups 0.95 > 0.9 would be a wrong order
    assert pairs == 2
    assert auc(y, score) == 0.75


def test_group_bootstrap_is_seeded_and_brackets_the_estimate() -> None:
    rng = np.random.default_rng(3)
    groups = [f"g{i % 6}" for i in range(120)]
    y = [i % 2 for i in range(120)]
    score = [0.6 * yi + rng.random() for yi in y]

    def statistic(ix: list[int]) -> float | None:
        return auc([y[i] for i in ix], [score[i] for i in ix])

    first = group_bootstrap(groups, statistic, n=200)
    assert first == group_bootstrap(groups, statistic, n=200)
    assert first[0] is not None and first[1] is not None
    assert first[0] <= auc(y, score) <= first[1]
    assert group_bootstrap(["a"] * 4, statistic) == (None, None)


def test_group_bootstrap_mean_is_in_points() -> None:
    lower, upper = group_bootstrap_mean([1.0, 0.0, 1.0, 1.0], ["a", "a", "b", "b"], n=500)

    assert lower is not None and upper is not None
    assert 50 <= lower <= upper <= 100


def test_cramer_v() -> None:
    assert cramer_v(["x", "x", "y", "y"], ["a", "a", "b", "b"]) == pytest.approx(1.0)
    assert cramer_v(["x", "x"], ["a", "b"]) is None


def test_shuffle_permutes_only_the_pool_and_is_seeded() -> None:
    labels = list(range(10))

    first = shuffle_labels(labels, [2, 3, 4, 5, 6], 0)

    assert first.tolist()[:2] == [0, 1] and first.tolist()[7:] == [7, 8, 9]
    assert sorted(first.tolist()[2:7]) == [2, 3, 4, 5, 6]
    assert first.tolist() == shuffle_labels(labels, [2, 3, 4, 5, 6], 0).tolist()
    assert first.tolist() != shuffle_labels(labels, [2, 3, 4, 5, 6], 1).tolist()


# One line from given predictions


def _setup() -> tuple[LineSetup, list[int]]:
    # Two classes: 0 normal, 1 fault; 4 groups, one group per fold.
    labels = [i % 2 for i in range(16)]
    groups = {i: f"g{i // 4}" for i in range(16)}
    folds = [
        Fold(fit=[i for i in range(16) if i // 4 != k], test=[i for i in range(16) if i // 4 == k])
        for k in range(4)
    ]
    return LineSetup(line="AURSAD", prior="frequency", folds=folds, groups=groups), labels


def _probabilities(labels: list[int], index: list[int], good: bool) -> np.ndarray:
    rng = np.random.default_rng(len(index))
    p_fault = [0.8 + 0.1 * rng.random() if labels[i] else 0.1 * rng.random() for i in index]
    if not good:
        p_fault = list(rng.random(len(index)))
    return np.array([[1 - p, p] for p in p_fault])


def _predictions(setup: LineSetup, labels: list[int], shuffles: int) -> Predictions:
    real = {k: [_probabilities(labels, f.test, good=True)] * 3 for k, f in enumerate(setup.folds)}
    shuffled = {
        r: {k: _probabilities(labels, f.test, good=False) for k, f in enumerate(setup.folds)}
        for r in range(shuffles)
    }
    return Predictions(real=real, repeat={0: real[0][0].copy()}, shuffled=shuffled)


def test_separable_line_opens_anomaly_but_not_cause() -> None:
    setup, labels = _setup()

    result, scores = evaluate(
        setup, _predictions(setup, labels, 19), labels, ["aursad"] * 16, ["normal", "fault"], 0
    )

    assert result["anomaly"]["auroc"] == 1.0
    assert result["anomaly"]["open"]
    # With one fault class the model's top 3 equals the prior's: no gain over the prior.
    assert result["cause"]["difference"] == 0.0 and not result["cause"]["open"]
    assert result["k6"]["passed"] and result["k6"]["p"] == 0.05
    assert result["k7"] == {"passed": True, "changed": 0}
    assert result["decision"] == "conditional" and result["verdict"] == "screening"
    assert len(scores) == 16 and {s.fold for s in scores} == {0, 1, 2, 3}


def test_fewer_than_19_shuffles_close_the_line() -> None:
    setup, labels = _setup()

    result, _ = evaluate(
        setup, _predictions(setup, labels, 3), labels, ["aursad"] * 16, ["normal", "fault"], 0
    )

    assert not result["k6"]["passed"]
    assert result["verdict"] == "closed"


def test_a_changed_repeat_fails_k7() -> None:
    setup, labels = _setup()
    predictions = _predictions(setup, labels, 19)
    predictions.repeat[0] = 1 - predictions.repeat[0]

    result, _ = evaluate(setup, predictions, labels, ["aursad"] * 16, ["normal", "fault"], 0)

    assert result["k7"]["changed"] == 4
    assert result["decision"] == "do_not_deploy"
