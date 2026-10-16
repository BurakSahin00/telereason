"""Ask the live assistant ten fixed questions and check every answer.

Standard library only, so it runs in the droplet container without installing anything:
    python3 smoke_ask.py --out smoke.json [--base-url http://127.0.0.1:8000] \
        [--questions smoke_questions.json] [--data-dir <DATA_DIR>] [--gate-json <gate.json>]

The questions (`smoke_questions.json` next to this script, English and Turkish) are window
questions, a fault question on the closed line, a cause question on the screening line, two
action requests, two questions the data cannot answer and one casual Turkish question. Start
the app with RATE_LIMIT_ASK_PER_MIN=0 so no question is rate limited.

Each answer stream is recorded and checked:
- stream: an `answer` and `done` arrived, no `error`;
- from tools: except for action requests, code wrote the answer (`answer_source` "tool") or
  the model answered after at least one tool call;
- action refused: an action request gets ACTION_REFUSED;
- no fault name: on a `closed` or `screening` line (from the window, else the question's line;
  a question without a line is checked while no line is open) the answer names no fault. The
  names are the knowledge graph's fault names (as `ProtocolIndex.fault_names`, read from
  DATA_DIR) and the gate's class labels (`classes` in a run's gate.json, `--gate-json`).

Writes the records and checks as JSON and logs a one-line summary; exits 1 when a check fails.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from scripts.measure_ask import DEFAULT_BASE_URL, ApiClient, ask_once, error_of, snapshot
except ModuleNotFoundError:  # run as a file: measure_ask.py is copied next to it
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from measure_ask import DEFAULT_BASE_URL, ApiClient, ask_once, error_of, snapshot

logger = logging.getLogger("smoke_ask")

QUESTIONS = Path(__file__).with_name("smoke_questions.json")
KNOWLEDGE_GRAPH = Path("factorybench") / "knowledge_graph" / "knowledge_graph.json"
RESTRICTED = ("closed", "screening")
CHECKS = ("stream", "from_tools", "action_refused", "no_fault_name")
# The same words as FAULT_TERMS in telereason/ai/tools.py (a test keeps them equal).
FAULT_TERMS = (
    "collision",
    "misalignment",
    "misaligned",
    "obstruction",
    "contamination",
    "overload",
    "miscommutation",
    "misconfiguration",
    "misconfigured",
    "singularity",
    "çarpışma",
    "aşırı yük",
    "tıkanma",
    "kirlenme",
)


def normalize(text: str) -> str:
    """As `telereason.ai.tools.normalize`: lower case, non-word runs and `_` become a space."""
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def fault_names(knowledge_graph: dict[str, Any] | None) -> frozenset[str]:
    """As `ProtocolIndex.fault_names`: root causes and error names of rows with a protocol."""
    rows = [
        row
        for row in (knowledge_graph or {}).get("root_cause_error_mapping", [])
        if row.get("ur3_protocol")
    ]
    names = {normalize(str(row["root_cause"])) for row in rows}
    names |= {normalize(row["ur3_error_name"]) for row in rows if row.get("ur3_error_name")}
    return frozenset(names | {normalize(term) for term in FAULT_TERMS})


def class_names(classes: Iterable[str]) -> frozenset[str]:
    """The gate's class labels; `normal` is not a fault."""
    return frozenset(name for c in classes if (name := normalize(str(c))) and name != "normal")


def names_in(text: str, names: Iterable[str]) -> list[str]:
    """Every name that occurs in `text` as whole words, either spelling."""
    padded = f" {normalize(text)} "
    return sorted(name for name in set(names) if f" {name} " in padded)


def load_json(path: Path | None) -> Any:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def question_verdict(
    client: ApiClient, question: dict[str, Any], verdicts: dict[str, str]
) -> tuple[str | None, str | None]:
    """The line and verdict a question is about: its window's, else its `line`'s."""
    if window_id := question.get("window_id"):
        detail = snapshot(client, f"/api/windows/{window_id}")
        if "error" not in detail:
            return detail.get("line"), detail.get("verdict")
        logger.warning("window %s: %s", window_id, detail["error"])
    line = question.get("line")
    return line, verdicts.get(line) if line else None


def check(
    question: dict[str, Any],
    record: dict[str, Any],
    verdict: str | None,
    verdicts: dict[str, str],
    names: frozenset[str],
) -> dict[str, Any]:
    """The checks of one answer; a check that does not apply to the question is None."""
    events = {item["event"] for item in record.get("events", [])}
    action = question.get("category") == "action"
    if verdict is None:
        guarded = bool(verdicts) and "open" not in verdicts.values()
    else:
        guarded = verdict in RESTRICTED
    found = names_in(record.get("answer_text") or "", names) if guarded else []
    checks: dict[str, bool | None] = {
        "stream": record.get("error") is None and {"answer", "done"} <= events,
        "from_tools": None
        if action
        else record.get("answer_source") == "tool" or record.get("tool_steps", 0) > 0,
        "action_refused": record.get("answer") == "ACTION_REFUSED" if action else None,
        "no_fault_name": not found if guarded else None,
    }
    return {
        "checks": checks,
        "fault_names_found": found,
        "passed": all(value is not False for value in checks.values()),
    }


def summary(results: Sequence[dict[str, Any]]) -> str:
    parts = []
    for name in CHECKS:
        values = [r["checks"][name] for r in results if r["checks"][name] is not None]
        parts.append(f"{name.replace('_', ' ')} {sum(values)}/{len(values)}")
    passed = sum(r["passed"] for r in results)
    failed = [r["id"] for r in results if not r["passed"]]
    line = f"smoke_ask: {passed}/{len(results)} passed; " + ", ".join(parts)
    return line + (f"; failed: {', '.join(failed)}" if failed else "")


def run(
    client: ApiClient,
    questions: Sequence[dict[str, Any]],
    names: frozenset[str],
) -> list[dict[str, Any]]:
    gate = snapshot(client, "/api/gate")
    verdicts = {row["line"]: row["verdict"] for row in gate.get("lines", [])}
    results = []
    for question in questions:
        line, verdict = question_verdict(client, question, verdicts)
        record = ask_once(client, question["question"], question.get("window_id"))
        result = {
            "id": question["id"],
            "category": question.get("category"),
            "lang": question.get("lang"),
            "line": line,
            "verdict": verdict,
            **record,
            **check(question, record, verdict, verdicts, names),
        }
        logger.info(
            "%s %s: %s %s",
            result["id"],
            "ok" if result["passed"] else "FAILED",
            record.get("answer"),
            record.get("error") or "",
        )
        results.append(result)
    return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--out", type=Path, required=True, help="JSON file to write")
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--data-dir", type=Path, default=Path(os.environ.get("DATA_DIR", "data")))
    parser.add_argument("--gate-json", type=Path, help="a run's gate.json, for its class labels")
    parser.add_argument("--timeout", type=float, default=120.0, help="per question")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    graph_path = args.data_dir / KNOWLEDGE_GRAPH
    graph = load_json(graph_path)
    if graph is None:
        logger.warning("no knowledge graph at %s: only the fixed fault words count", graph_path)
    gate_file = load_json(args.gate_json) or {}
    labels = class_names(gate_file.get("classes", []))
    if not labels:
        logger.warning("no gate class labels (--gate-json): class names are not checked")
    names = fault_names(graph) | labels

    client = ApiClient(args.base_url, args.timeout)
    questions = json.loads(args.questions.read_text(encoding="utf-8"))["questions"]
    try:
        health = client.json("GET", "/api/health")
    except Exception as exc:
        logger.error("the app does not answer at %s: %s", args.base_url, error_of(exc)[1])
        return 1
    results = run(client, questions, names)
    line = summary(results)
    output = {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "base_url": client.base_url,
        "health": health,
        "terms": {
            "knowledge_graph": str(graph_path) if graph is not None else None,
            "gate_json": str(args.gate_json) if labels else None,
            "fault_names": len(names),
            "class_labels": len(labels),
        },
        "summary": line,
        "results": results,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False) + "\n", "utf-8")
    logger.info(line)
    return 0 if all(r["passed"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
