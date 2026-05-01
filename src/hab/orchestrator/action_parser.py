"""Lenient parsing for agent action JSON."""
from __future__ import annotations

import json

from hab.engine.actions import Action


def parse_action_lenient(raw: str, hand_id: str | None) -> Action:
    """Parse agent output, accepting common LLM JSON-format deviations."""
    s = raw.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines).strip()
    if not s.startswith("{"):
        first = s.find("{")
        last = s.rfind("}")
        if first >= 0 and last > first:
            s = s[first:last + 1]
    data = json.loads(s)
    if "action" not in data:
        for alias in ("type", "decision", "move", "choice", "play", "action_type"):
            if alias in data:
                data["action"] = data[alias]
                break
    if "hand_id" not in data and hand_id:
        data["hand_id"] = hand_id
    a = data.get("action")
    if isinstance(a, str):
        a = a.lower().strip()
        if a == "bet":
            data["action"] = "raise"
        elif a in ("all-in", "allin"):
            data["action"] = "all_in"
        else:
            data["action"] = a
    return Action.model_validate(data)
