"""Channel selection for the night run (setting B) and plan-versus-measured channel pairs.

Setting B drops the event channel, state channels and every temperature channel; the gate,
the facts and the signal band all use the channels that remain.  The pairs match a planned
channel (``setpoint_pos_5``) with its measured twin (``feedback_pos_5``) by name; the gap is
measured minus planned.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable, Sequence

STATE_CHANNELS = (
    r"^(event|task_phase|robot_mode|runtime_state|safety_mode|digital_input_bits"
    r"|digital_output_bits|joint_mode_\d+)$"
)
SETTING_B = re.compile(STATE_CHANNELS + r"|temp")
# The 11 Oct vocabulary kept the 160 most frequent names; the pool has fewer, so it is a cap.
VOCABULARY_SIZE = 160

PLAN_WORDS = frozenset(
    {"setpoint", "target", "command", "commanded", "desired", "reference", "ref", "planned"}
)
MEASURED_WORDS = frozenset({"feedback", "actual", "measured", "est"})


def vocabulary(first_rows: Iterable[Iterable[str]], size: int = VOCABULARY_SIZE) -> list[str]:
    """Most frequent channel names over the windows' first rows, by count then name."""
    counts: Counter[str] = Counter()
    for names in first_rows:
        counts.update(name for name in names if name != "t")
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [name for name, _ in ranked[:size]]


def setting_b(channels: Iterable[str]) -> list[str]:
    """Channels kept by setting B, in the given order."""
    return [name for name in channels if not SETTING_B.search(name)]


def _pair_key(channel: str) -> tuple[str | None, str]:
    parts = [part for part in re.split(r"[_\W]+", channel.lower()) if part]
    kind = None
    if any(part in PLAN_WORDS for part in parts):
        kind = "plan"
    elif any(part in MEASURED_WORDS for part in parts):
        kind = "measured"
    rest = "_".join(part for part in parts if part not in PLAN_WORDS | MEASURED_WORDS)
    return kind, rest


def plan_measured_pairs(channels: Sequence[str]) -> list[tuple[str, str]]:
    """(planned, measured) pairs whose names match once the plan or measured word is removed.

    When several channels share a key, the alphabetically first planned and measured ones pair.
    Pairs are sorted by the shared key.
    """
    groups: dict[str, dict[str, list[str]]] = {}
    for channel in channels:
        kind, key = _pair_key(channel)
        if kind and key:
            groups.setdefault(key, {"plan": [], "measured": []})[kind].append(channel)
    return [
        (sorted(group["plan"])[0], sorted(group["measured"])[0])
        for _, group in sorted(groups.items())
        if group["plan"] and group["measured"]
    ]


def pair_name(planned: str, measured: str) -> str:
    """How a pair is named in facts and notes: ``feedback_pos_5 vs setpoint_pos_5``."""
    return f"{measured} vs {planned}"
