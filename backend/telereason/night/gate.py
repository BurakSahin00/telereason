"""The nightly gate: XGBoost on grouped folds, then rules v0 decide each line's verdict.

Run it as its own process, before the scan:

    python -m telereason.night.gate --out <dir> [--data-dir <DATA_DIR>]

It writes ``gate.json`` (rules, device, XGBoost version, trainings, seconds and every check per
line) and ``scores.jsonl`` (one out-of-fold score per tested window).  The night run reads only
these files: XGBoost uses OpenMP, which must not share a process with the vLLM engine, and the
gate slows the scan down when both run at once.

Per line: 5 folds x 3 seeds with the real labels, fold 0 seed 0 once more (K7), and
``GATE_K6_PERMUTATIONS`` shuffles x 5 folds with seed 0 (K6, default 19).  With the defaults
that is 111 trainings per line, as on 11 Oct.  Fewer shuffles make a quick run, but K6 then
cannot pass and every line is closed.

``GATE_DEVICE``: ``auto`` (default) tries a small GPU training and falls back to the CPU,
``cuda`` asks for the GPU (ROCm XGBoost also calls it ``cuda``), ``cpu`` stays on the CPU.
The device actually used is written to ``gate.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from telereason.night.features import gate_features
from telereason.night.folds import LineSetup, line_setups
from telereason.night.gate_rules import (
    RULES_V0,
    Predictions,
    Rules,
    evaluate,
    shuffle_labels,
)
from telereason.night.windows import Pool, load_pool
from telereason.settings import load_settings

log = logging.getLogger(__name__)

XGB_PARAMS: dict[str, Any] = {
    "objective": "multi:softprob",
    "tree_method": "hist",
    "max_depth": 6,
    "n_estimators": 100,
    "learning_rate": 0.2,
    "max_bin": 128,
}
SEEDS = (0, 1, 2)
DEVICES = ("auto", "cuda", "cpu")
NORMAL = "normal"
GATE_FILE = "gate.json"
SCORES_FILE = "scores.jsonl"


def requested_device() -> str:
    device = os.environ.get("GATE_DEVICE", "auto").strip().lower() or "auto"
    if device not in DEVICES:
        raise ValueError(f"GATE_DEVICE must be one of {DEVICES}, got {device!r}")
    return device


def k6_permutations(default: int = RULES_V0.k6_permutations) -> int:
    value = os.environ.get("GATE_K6_PERMUTATIONS", "").strip()
    count = int(value) if value else default
    if count < 0:
        raise ValueError("GATE_K6_PERMUTATIONS must be 0 or more")
    return count


def effective_device(model: Any) -> str | None:
    """The device XGBoost really used; it falls back to the CPU with only a warning."""
    try:
        config = json.loads(model.get_booster().save_config())
        return config["learner"]["generic_param"].get("device")
    except Exception:  # noqa: BLE001 - any failure means the device is unknown
        return None


def pick_device(requested: str) -> str:
    """``cuda`` or ``cpu``; ``auto`` keeps ``cuda`` only when a small GPU training runs there."""
    if requested != "auto":
        return requested
    import xgboost as xgb

    rng = np.random.default_rng(0)
    x = rng.normal(size=(2000, 16)).astype("float32")
    y = (x[:, 0] > 0).astype(int)
    try:
        model = xgb.XGBClassifier(n_estimators=5, tree_method="hist", max_depth=3, device="cuda")
        model.fit(x, y)
    except Exception as exc:  # noqa: BLE001 - no GPU build or no GPU: use the CPU
        log.info("gate: GPU training failed (%s); using the CPU", exc)
        return "cpu"
    device = effective_device(model) or ""
    return "cuda" if device.startswith("cuda") else "cpu"


def train_predict(
    x: np.ndarray,
    fit: Sequence[int],
    test: Sequence[int],
    labels: Sequence[int],
    n_classes: int,
    seed: int,
    device: str,
) -> tuple[np.ndarray, str | None]:
    """Train on ``fit`` and return P(class) for ``test`` over all classes, and the device used."""
    import xgboost as xgb

    present = sorted({int(labels[i]) for i in fit})
    out = np.zeros((len(test), n_classes))
    if len(present) == 1:
        out[:, present[0]] = 1.0
        return out, None
    code = {c: k for k, c in enumerate(present)}
    params = dict(XGB_PARAMS, random_state=seed, device=device, n_jobs=os.cpu_count() or 1)
    if len(present) == 2:
        params["objective"] = "binary:logistic"
    model = xgb.XGBClassifier(**params)
    model.fit(x[list(fit)], np.array([code[int(labels[i])] for i in fit]))
    out[:, present] = model.predict_proba(x[list(test)])
    return out, effective_device(model)


def jobs(setup: LineSetup, shuffles: int) -> list[tuple[str, int, int, int]]:
    """(kind, fold, seed, shuffle) in the 11 Oct order: real, K7 repeat, shuffles."""
    out = [("real", k, seed, -1) for k in range(len(setup.folds)) for seed in SEEDS]
    out.append(("repeat", 0, 0, -1))
    out += [("shuffled", k, 0, r) for r in range(shuffles) for k in range(len(setup.folds))]
    return out


def run_line(
    x: np.ndarray,
    labels: Sequence[int],
    n_classes: int,
    setup: LineSetup,
    device: str,
    shuffles: int,
) -> tuple[Predictions, int, set[str]]:
    predictions = Predictions(real={}, repeat={}, shuffled={})
    shuffled_labels: dict[int, np.ndarray] = {}
    used: set[str] = set()
    todo = jobs(setup, shuffles)
    for kind, k, seed, shuffle in todo:
        fold = setup.folds[k]
        y: Sequence[int] = labels
        if kind == "shuffled":
            if shuffle not in shuffled_labels:
                shuffled_labels[shuffle] = shuffle_labels(labels, setup.pool, shuffle)
            y = shuffled_labels[shuffle]
        p, device_used = train_predict(x, fold.fit, fold.test, y, n_classes, seed, device)
        used.add(device_used or "unknown")
        if kind == "real":
            predictions.real.setdefault(k, []).append(p)
        elif kind == "repeat":
            predictions.repeat[k] = p
        else:
            predictions.shuffled.setdefault(shuffle, {})[k] = p
    return predictions, len(todo), used


def run_gate(
    pool: Pool,
    device: str = "cpu",
    shuffles: int = RULES_V0.k6_permutations,
    rules: Rules = RULES_V0,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The gate document and one score record per tested window."""
    import xgboost as xgb

    started = time.perf_counter()
    windows = pool.windows
    classes = sorted({w.label for w in windows})
    if NORMAL not in classes:
        raise ValueError("the pool has no normal windows; the gate cannot score")
    code = {c: k for k, c in enumerate(classes)}
    labels = [code[w.label] for w in windows]
    tasks = [w.task for w in windows]
    normal = code[NORMAL]
    x = gate_features(pool)
    features_s = time.perf_counter() - started

    lines: dict[str, Any] = {}
    scores: list[dict[str, Any]] = []
    trainings = 0
    used: set[str] = set()
    for setup in line_setups(windows, labels):
        line_started = time.perf_counter()
        predictions, count, devices = run_line(x, labels, len(classes), setup, device, shuffles)
        result, line_scores = evaluate(setup, predictions, labels, tasks, classes, normal, rules)
        seconds = time.perf_counter() - line_started
        lines[setup.line] = {**result, "trainings": count, "seconds": round(seconds, 1)}
        trainings += count
        used |= devices
        for s in line_scores:
            window = windows[s.index]
            scores.append(
                {
                    "window_id": window.window_id,
                    "ms_id": window.ms_id,
                    "line": setup.line,
                    "group": setup.groups[s.index],
                    "fold": s.fold,
                    "p_normal": s.p_normal,
                    "score": 1 - s.p_normal,
                }
            )
        log.info(
            "gate %s: %s (%s), AUROC %s, %d trainings in %.1f s",
            setup.line,
            result["decision"],
            result["verdict"],
            result["anomaly"]["auroc"],
            count,
            seconds,
        )

    document = {
        "rules": asdict(rules),
        "xgboost": xgb.__version__,
        "params": XGB_PARAMS,
        "seeds": list(SEEDS),
        "k6_permutations": shuffles,
        "device": device,
        "device_used": sorted(used),
        "windows": len(windows),
        "features": int(x.shape[1]),
        "classes": classes,
        "trainings": trainings,
        "seconds": {"features": round(features_s, 1), "total": 0.0},
        "lines": lines,
    }
    document["seconds"]["total"] = round(time.perf_counter() - started, 1)
    return document, scores


def write_outputs(out: Path, document: dict[str, Any], scores: list[dict[str, Any]]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / GATE_FILE).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    with open(out / SCORES_FILE, "w", encoding="utf-8", newline="\n") as handle:
        for record in scores:
            handle.write(json.dumps(record) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m telereason.night.gate", description=__doc__)
    parser.add_argument("--out", required=True, type=Path, help="directory for the output files")
    parser.add_argument("--data-dir", help="DATA_DIR with factorybench/ (default: settings)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    started = time.perf_counter()
    data_dir = args.data_dir or load_settings().data_dir
    device = pick_device(requested_device())
    shuffles = k6_permutations()
    pool = load_pool(data_dir)
    load_s = time.perf_counter() - started
    document, scores = run_gate(pool, device=device, shuffles=shuffles)
    document["device_requested"] = requested_device()
    document["seconds"]["load"] = round(load_s, 1)
    document["seconds"]["wall"] = round(time.perf_counter() - started, 1)
    write_outputs(args.out, document, scores)
    log.info(
        "gate: %d scores written to %s in %.1f s on %s",
        len(scores),
        args.out,
        document["seconds"]["wall"],
        ",".join(document["device_used"]) or device,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
