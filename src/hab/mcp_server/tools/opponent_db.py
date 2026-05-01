"""Opponent stats derived from session-level hand history JSON files.

Stats:
- VPIP: % hands voluntarily put money in pot (any non-blind preflop call/raise)
- PFR: % hands raised preflop
- 3-bet: % preflop reraise
- AF (aggression factor): (raises + bets) / calls postflop
- WTSD: went to showdown %
"""
from __future__ import annotations

import json
from pathlib import Path


def _load_hands(session_dir: Path) -> list[dict]:
    hands_dir = session_dir / "hands"
    if not hands_dir.exists():
        return []
    out = []
    for p in sorted(hands_dir.glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            continue
    return out


def opponent_database_query(session_dir: Path, opponent_id: str, filters: dict | None = None) -> dict:
    """Compute opponent stats from session_dir/hands/*.json."""
    hands = _load_hands(session_dir)
    filters = filters or {}

    total_hands = 0
    vpip_count = 0
    pfr_count = 0
    three_bet_count = 0
    aggressive_postflop = 0
    passive_postflop = 0
    showdowns = 0

    for h in hands:
        actions = h.get("action_history", [])
        players_in_hand = set((h.get("stack_deltas") or {}).keys())
        if opponent_id not in players_in_hand:
            # Fallback for old hand records that did not include zero-delta players.
            players_in_hand = {a.get("player_id") for a in actions}
        if opponent_id not in players_in_hand:
            continue
        total_hands += 1

        # Preflop subset
        pf = [a for a in actions if a.get("street") == "preflop"]
        opp_pf = [a for a in pf if a.get("player_id") == opponent_id]
        # VPIP: any voluntary call or raise preflop
        if any(a["action"] in ("call", "raise", "bet", "all_in") for a in opp_pf):
            vpip_count += 1
        if any(a["action"] in ("raise", "bet") for a in opp_pf):
            pfr_count += 1
        # 3-bet detection: count preflop raises in order, opp's raise is 3+bet if >= 2nd raise overall
        raise_idx = 0
        for a in pf:
            if a["action"] in ("raise", "bet"):
                raise_idx += 1
                if a["player_id"] == opponent_id and raise_idx >= 2:
                    three_bet_count += 1
                    break

        # Postflop aggression
        for a in actions:
            if a.get("player_id") != opponent_id:
                continue
            if a.get("street") == "preflop":
                continue
            if a["action"] in ("raise", "bet"):
                aggressive_postflop += 1
            elif a["action"] == "call":
                passive_postflop += 1

        # WTSD is approximate until hand records include an explicit showdown flag.
        opp_folded = any(a.get("player_id") == opponent_id and a.get("action") == "fold" for a in actions)
        if not opp_folded and len(h.get("board") or []) == 5:
            showdowns += 1

    if total_hands == 0:
        return {
            "opponent_id": opponent_id,
            "hands_observed": 0,
            "vpip": None,
            "pfr": None,
            "three_bet": None,
            "af": None,
            "wtsd": None,
            "confidence": "no_data",
        }

    af = aggressive_postflop / passive_postflop if passive_postflop > 0 else None
    confidence = "low" if total_hands < 30 else ("medium" if total_hands < 100 else "high")
    return {
        "opponent_id": opponent_id,
        "hands_observed": total_hands,
        "vpip": round(vpip_count / total_hands, 4),
        "pfr": round(pfr_count / total_hands, 4),
        "three_bet": round(three_bet_count / total_hands, 4),
        "af": round(af, 3) if af is not None else None,
        "wtsd": round(showdowns / total_hands, 4),
        "confidence": confidence,
    }
