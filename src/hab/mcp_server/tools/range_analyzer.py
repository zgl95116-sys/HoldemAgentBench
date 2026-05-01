"""Estimate opponent range from observed VPIP + action sequence.

Output is a real range spec string that can be fed back into equity_calculator,
e.g. "TT+,AJs+,KQs,AKo".

The mapping uses solver-derived population norms; tighter VPIP → narrower range.
Action context (open vs 3-bet vs call) further narrows.
"""
from __future__ import annotations

from hab.mcp_server.tools.range_parser import range_density


# VPIP threshold → preset name to use as range
_VPIP_TO_PRESET: list[tuple[float, str]] = [
    (0.10, "tight"),                                              # ~5%
    (0.15, "TT+,AJs+,KQs,AQo+"),                                  # ~5-7%
    (0.20, "77+,A9s+,KTs+,QJs,AJo+,KQo"),                         # ~10%
    (0.25, "55+,A7s+,KTs+,QTs+,JTs,T9s,98s,ATo+,KJo+,QJo"),       # ~14%
    (0.30, "33+,A2s+,K9s+,Q9s+,J9s+,T9s,98s,87s,76s,65s,A8o+,KTo+,QTo+,JTo"),  # ~22%
    (0.40, "22+,A2s+,K7s+,Q9s+,J9s+,T8s+,97s+,86s+,76s,65s,54s,A6o+,KTo+,Q9o+,J9o+,T9o,98o"),  # ~30%
    (0.50, "22+,A2s+,K2s+,Q2s+,J5s+,T6s+,96s+,85s+,75s+,64s+,54s,A2o+,K5o+,Q8o+,J8o+,T8o+,98o"),  # ~49%
    (0.65, "HU_SB_open"),                                         # ~67%
    (1.00, "any_two"),                                            # 100%
]


def range_analyzer(
    opponent_id: str,
    action_sequence: list[dict] | None = None,
    board: list[str] | None = None,
    position: str | None = None,
    stack_depth_bb: int = 100,
    observed_vpip: float | None = None,
) -> dict:
    """Estimate the opponent's range. Returns a parseable range string + density.

    Heuristics:
      1. VPIP → base range (e.g. VPIP 0.22 → TAG range)
      2. Action sequence narrows it further:
         - open-raised: keep top half of base range
         - 3-bet preflop: tighten to TT+/AKs/AKo-ish
         - barreled flop+turn: narrow to value+strong draws
    """
    if observed_vpip is None:
        observed_vpip = 0.22  # default TAG profile

    # Pick base range from VPIP
    base_range = "tight"
    for threshold, preset in _VPIP_TO_PRESET:
        if observed_vpip <= threshold:
            base_range = preset
            break

    # Action-based narrowing
    actions = action_sequence or []

    def is_opponent_action(action: dict) -> bool:
        # Historical callers often omitted player_id; treat those as already
        # scoped to the opponent for backward compatibility.
        pid = action.get("player_id")
        return pid is None or pid == opponent_id

    opponent_actions = [a for a in actions if is_opponent_action(a)]
    pf_raises = sum(
        1 for a in opponent_actions
        if a.get("street") == "preflop" and a.get("action") in ("raise", "bet")
    )

    pf_three_bets = 0
    raises_seen = 0
    for a in actions:
        if a.get("street") != "preflop" or a.get("action") not in ("raise", "bet"):
            continue
        raises_seen += 1
        if is_opponent_action(a) and raises_seen >= 2:
            pf_three_bets += 1

    barrels_postflop = sum(
        1 for a in opponent_actions
        if a.get("street") in ("flop", "turn", "river")
        and a.get("action") in ("raise", "bet")
    )

    narrowed = base_range
    notes: list[str] = []

    if pf_three_bets >= 1:
        narrowed = "TT+,AJs+,KQs,AKo"
        notes.append(f"3-bet preflop → narrowed to value+blockers ({narrowed})")
    elif pf_raises >= 1:
        notes.append(f"Open-raised → keeping base VPIP-derived range ({narrowed})")

    if barrels_postflop >= 2:
        # Triple-barrel range: pure value
        narrowed = "TT+,AKs,AKo,QQ"
        notes.append("Multi-street barrel → tightened to value range")

    return {
        "opponent_id": opponent_id,
        "estimated_range": narrowed,
        "range_density": round(range_density(narrowed), 3),
        "based_on_vpip": observed_vpip,
        "preflop_raises_observed": pf_raises,
        "postflop_aggressive_actions": barrels_postflop,
        "opponent_actions_considered": len(opponent_actions),
        "notes": notes,
        "usage": (
            "Pass `estimated_range` as `opponent_range` to equity_calculator "
            "for a realistic equity estimate against THIS opponent."
        ),
    }
