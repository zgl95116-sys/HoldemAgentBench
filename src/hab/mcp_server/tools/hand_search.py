"""Naive keyword/filter search over hand history JSON.

For MVP this just scans, filters by opponent_id and substring match in actions
or board. Future: real semantic search.
"""
from __future__ import annotations

import json
from pathlib import Path


_HIDDEN_KEYS = {"hole_cards", "showdown_cards"}


def _public_hand_view(hand: dict) -> dict:
    """Return only information an agent should be allowed to search."""
    return {
        k: v
        for k, v in hand.items()
        if k not in _HIDDEN_KEYS
    }


def _searchable_public_text(hand: dict) -> str:
    """Search public values only; JSON key names can create false matches."""
    values: list[str] = []
    for key in ("hand_id", "winner", "pot"):
        if hand.get(key) is not None:
            values.append(str(hand[key]))
    values.extend(str(card) for card in hand.get("board") or [])
    for pid, delta in (hand.get("stack_deltas") or {}).items():
        values.append(str(pid))
        values.append(str(delta))
    for action in hand.get("action_history") or []:
        for key in ("player_id", "street", "action", "amount", "reason"):
            if action.get(key) is not None:
                values.append(str(action[key]))
    return " ".join(values).lower()


def hand_history_search(
    session_dir: Path,
    query: str = "",
    opponent_id: str | None = None,
    limit: int = 5,
) -> list[dict]:
    hands_dir = session_dir / "hands"
    if not hands_dir.exists():
        return []
    q = query.lower()
    out: list[dict] = []
    for p in sorted(hands_dir.glob("*.json"), reverse=True):
        try:
            h = json.loads(p.read_text())
        except Exception:
            continue
        if opponent_id and opponent_id not in (h.get("stack_deltas") or {}):
            continue
        public_h = _public_hand_view(h)
        blob = _searchable_public_text(public_h)
        if q and q not in blob:
            continue
        out.append({
            "hand_id": h.get("hand_id"),
            "winner": h.get("winner"),
            "pot": h.get("pot"),
            "board": h.get("board"),
            "stack_deltas": h.get("stack_deltas"),
            "actions_summary": [
                f"{a.get('player_id')}:{a.get('street')}:{a.get('action')}"
                for a in public_h.get("action_history", [])
            ],
        })
        if len(out) >= limit:
            break
    return out
