"""Preflop chart lookup with mixed-frequency support.

For each scenario, hand classes have a `raise_freq` (and implicitly fold = 1 - raise_freq).
These are solver-ballpark chart values, not live solver output.

Usage:
  gto_lookup("HU_SB_open", "open", ["7c", "9d"]) →
    {scenario: ..., hand: "97o", raise_freq: 0.55, action: "raise" (since >0.5)}

If a hand isn't explicitly listed in a scenario, default raise_freq=0 (fold).
"""
from __future__ import annotations

from hab.mcp_server.tools.range_parser import parse_range


def _hand_key(cards: list[str]) -> str:
    if len(cards) != 2:
        return ""
    rank_order = "23456789TJQKA"
    r1, s1 = cards[0][0], cards[0][1]
    r2, s2 = cards[1][0], cards[1][1]
    if r1 == r2:
        return r1 + r2
    if rank_order.index(r1) < rank_order.index(r2):
        r1, r2, s1, s2 = r2, r1, s2, s1
    suited = "s" if s1 == s2 else "o"
    return r1 + r2 + suited


# Each scenario maps a hand class → raise frequency [0.0, 1.0].
# Built from solver-derived ballparks (Pio/GTOWizard at 100bb).

def _from_range(range_str: str, freq: float = 1.0) -> dict[str, float]:
    """Quick helper: assign uniform freq to all hands in a range string."""
    return {h: freq for h in parse_range(range_str)}


# === Heads-up SB open (button=SB acts first) ===
_HU_SB_OPEN: dict[str, float] = {
    **_from_range("AA,KK,QQ,JJ,TT,99,88,77,66,55,44,33,22", 1.0),
    **_from_range("AKs,AQs,AJs,ATs,A9s,A8s,A7s,A6s,A5s,A4s,A3s,A2s", 1.0),
    **_from_range("KQs,KJs,KTs,K9s,K8s,K7s,K6s,K5s,K4s,K3s,K2s", 1.0),
    **_from_range("QJs,QTs,Q9s,Q8s,Q7s,Q6s,Q5s,Q4s", 1.0),
    **_from_range("Q3s,Q2s", 0.7),
    **_from_range("JTs,J9s,J8s,J7s,J6s,J5s", 1.0),
    **_from_range("J4s,J3s,J2s", 0.6),
    **_from_range("T9s,T8s,T7s,T6s,T5s", 1.0),
    **_from_range("T4s,T3s,T2s", 0.4),
    **_from_range("98s,97s,96s,87s,86s,76s,75s,65s,64s,54s", 1.0),
    **_from_range("95s,85s,74s,53s,43s", 0.7),
    **_from_range("AKo,AQo,AJo,ATo,A9o,A8o,A7o,A6o,A5o,A4o,A3o,A2o", 1.0),
    **_from_range("KQo,KJo,KTo,K9o,K8o,K7o,K6o,K5o", 1.0),
    **_from_range("K4o,K3o,K2o", 0.6),
    **_from_range("QJo,QTo,Q9o,Q8o", 1.0),
    **_from_range("Q7o,Q6o,Q5o,Q4o", 0.5),
    **_from_range("JTo,J9o,J8o", 1.0),
    **_from_range("J7o,J6o", 0.55),
    **_from_range("T9o,T8o,T7o", 1.0),
    **_from_range("T6o", 0.45),
    **_from_range("98o,97o", 0.85),
    **_from_range("96o", 0.4),
    **_from_range("87o,86o", 0.7),
    **_from_range("76o,65o", 0.55),
    **_from_range("54o", 0.3),
}

# === HU BB defense vs SB open (3-bet | call | fold) ===
_HU_BB_VS_OPEN: dict[str, dict[str, float]] = {
    # 3bet frequencies
    **{h: {"three_bet": 0.95, "call": 0.05} for h in parse_range("AA,KK,QQ")},
    **{h: {"three_bet": 0.6, "call": 0.4} for h in parse_range("JJ,TT,AKs,AKo")},
    **{h: {"three_bet": 0.4, "call": 0.6} for h in parse_range("99,88,AQs,AQo,AJs,KQs")},
    **{h: {"three_bet": 0.2, "call": 0.8} for h in parse_range("77,66,55,AJo,ATs,A5s,A4s,KJs,QJs,JTs")},
    **{h: {"three_bet": 0.1, "call": 0.85, "fold": 0.05} for h in parse_range("44,33,22,A9s,A8s,KTs,K9s,QTs,Q9s,J9s,T9s,98s")},
    # Pure calls (mostly suited connectors, weak Ax, broadways)
    **{
        h: {"call": 0.95, "fold": 0.05}
        for h in parse_range(
            "A2o-ATo,K3o-KJo,Q5o-QTo,J7o-JTo,T7o-T9o,97o+,87o,76o,65o,"
            "A2s-A9s,K2s-K8s,Q2s-Q8s,J2s-J8s,T2s-T8s,87s,76s,65s,54s,43s"
        )
    },
}


# === 6-max ranges (UTG/HJ/CO/BTN/SB) — open frequencies only for MVP ===
_6M_UTG_OPEN: dict[str, float] = _from_range("6M_UTG_open", 1.0)
_6M_HJ_OPEN: dict[str, float] = _from_range("6M_HJ_open", 1.0)
_6M_CO_OPEN: dict[str, float] = _from_range("6M_CO_open", 1.0)
_6M_BTN_OPEN: dict[str, float] = _from_range("6M_BTN_open", 1.0)
_6M_SB_OPEN: dict[str, float] = _from_range("6M_SB_open", 1.0)


_OPEN_CHARTS: dict[str, dict[str, float]] = {
    "HU_SB_open": _HU_SB_OPEN,
    "6M_UTG_open": _6M_UTG_OPEN,
    "6M_HJ_open": _6M_HJ_OPEN,
    "6M_CO_open": _6M_CO_OPEN,
    "6M_BTN_open": _6M_BTN_OPEN,
    "6M_SB_open": _6M_SB_OPEN,
}


def gto_lookup(
    position_scenario: str,
    action_sequence: str,
    my_cards: list[str],
    stack_depth_bb: int = 100,
) -> dict:
    """Returns:
      For open scenarios: {action: 'raise' | 'fold', raise_freq: float, ...}
      For BB-vs-open scenario: {action: engine-legal action, frequencies: ...}

    Mixed frequencies are solver-ballpark chart values. We give the most
    frequent engine-legal action as `action`; agents can inspect
    `strategic_action` and `frequencies` for more context.
    """
    key = _hand_key(my_cards)
    if not key:
        return {"error": f"unparseable cards: {my_cards}"}

    # BB defense scenario
    if position_scenario == "HU_BB_vs_open":
        defense = _HU_BB_VS_OPEN.get(key, {"fold": 1.0})
        # Fill missing
        defense = {**{"three_bet": 0.0, "call": 0.0, "fold": 0.0}, **defense}
        # Pick mode
        strategic_action = max(("three_bet", "call", "fold"), key=lambda k: defense[k])
        action = "raise" if strategic_action == "three_bet" else strategic_action
        return {
            "scenario": position_scenario,
            "hand": key,
            "action": action,
            "engine_action": action,
            "strategic_action": strategic_action,
            "frequencies": defense,
            "stack_depth_bb": stack_depth_bb,
            "chart_stack_depth_bb": 100,
            "warning": (
                "Chart is calibrated for 100bb and does not solve the supplied "
                "action_sequence/stack depth dynamically."
                if stack_depth_bb != 100 or action_sequence else None
            ),
            "note": "Frequencies are solver-ballpark preflop chart values, not live solver output.",
        }

    # Open chart
    chart = _OPEN_CHARTS.get(position_scenario)
    if chart is None:
        return {
            "error": f"unknown scenario: {position_scenario}",
            "available_scenarios": list(_OPEN_CHARTS.keys()) + ["HU_BB_vs_open"],
        }

    raise_freq = chart.get(key, 0.0)
    action = "raise" if raise_freq >= 0.5 else "fold"
    return {
        "scenario": position_scenario,
        "hand": key,
        "action": action,
        "engine_action": action,
        "strategic_action": action,
        "raise_freq": round(raise_freq, 2),
        "fold_freq": round(1 - raise_freq, 2),
        "stack_depth_bb": stack_depth_bb,
        "chart_stack_depth_bb": 100,
        "warning": (
            "Chart is calibrated for 100bb and does not solve the supplied "
            "action_sequence/stack depth dynamically."
            if stack_depth_bb != 100 or action_sequence else None
        ),
        "note": "Mixed frequencies are solver-ballpark preflop chart values. "
                "Pick stochastically or default to majority action.",
    }
