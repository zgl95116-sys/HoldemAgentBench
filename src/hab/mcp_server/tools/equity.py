"""Monte Carlo equity calculator using pokerkit's hand evaluator + range support.

Improvements over MVP version:
- `opponent_range` accepts standard poker range strings ("AA,KK,AKs,A5s+") OR
  named presets ("HU_SB_open", "tight", "any_two", etc.)
- Sampled opponent hole cards come from the specified range, not random
- Returns the effective range size + density for transparency
"""
from __future__ import annotations

import random
from typing import Any

from pokerkit import Card, StandardHighHand

from hab.mcp_server.tools.range_parser import is_random_range, range_to_combos


_DECK = [r + s for r in "23456789TJQKA" for s in "cdhs"]
_DECK_SET = set(_DECK)


def _parse(cards: list[str]) -> list:
    out = []
    for s in cards:
        out.extend(list(Card.parse(s)))
    return out


def _validate_known_cards(my_cards: list[str], board: list[str]) -> str | None:
    known = list(my_cards) + list(board)
    invalid = [c for c in known if c not in _DECK_SET]
    if invalid:
        return f"invalid card(s): {invalid}"
    if len(set(known)) != len(known):
        return "duplicate cards in my_cards/board"
    return None


def _hand_rank(my: list, board: list):
    return StandardHighHand.from_game(my, board)


def _filter_combos(
    combos: list[tuple[str, str]], blocked: set[str]
) -> list[tuple[str, str]]:
    """Drop combos that conflict with blocked cards (my hole + visible board)."""
    return [c for c in combos if c[0] not in blocked and c[1] not in blocked]


def equity(
    my_cards: list[str],
    board: list[str] | None = None,
    opponent_range: str = "any_two",
    num_opponents: int = 1,
    simulations: int = 5000,
    seed: int | None = None,
) -> dict[str, Any]:
    """Returns:
      {
        "equity":     <win_rate + tie_split>,
        "win":        <pure win rate>,
        "tie":        <tie share>,
        "simulations_run": <int>,
        "opponent_range": <echoed string>,
        "opponent_combos": <how many distinct hands the range covers>,
        "range_density": <fraction of all 1326 hands>,
        "method":     "exhaustive" | "monte-carlo",
      }
    """
    if num_opponents < 1:
        return {"error": "num_opponents must be >= 1"}
    if num_opponents > 5:
        return {"error": "num_opponents must be <= 5 for tractable simulation"}
    if simulations <= 0:
        return {"error": "simulations must be > 0"}
    if len(my_cards) != 2:
        return {"error": "my_cards must be exactly 2"}
    board = board or []
    if len(board) not in (0, 3, 4, 5):
        return {"error": f"board must have 0/3/4/5 cards, got {len(board)}"}
    card_error = _validate_known_cards(my_cards, board)
    if card_error:
        return {"error": card_error}

    rng = random.Random(seed)
    used = set(my_cards) | set(board)

    # Resolve opponent range to concrete combos
    try:
        opp_combos = range_to_combos(opponent_range, strict=True)
    except ValueError as e:
        return {"error": str(e), "opponent_range": opponent_range}
    if not opp_combos:
        if not is_random_range(opponent_range):
            return {"error": f"empty or invalid opponent_range: {opponent_range}"}
        # Random / "any" → all 1326 combos minus blocked
        opp_combos = [
            (a, b)
            for a, b in (
                (a, b) for i, a in enumerate(_DECK) for b in _DECK[i + 1:]
            )
        ]
    opp_combos = _filter_combos(opp_combos, used)
    if not opp_combos:
        return {"error": "no valid opponent combos given blockers"}

    my_parsed = _parse(my_cards)
    cards_to_complete_board = 5 - len(board)

    wins = 0.0
    ties = 0.0
    actual_sims = 0

    for _ in range(simulations):
        # Pick opponent's hand from their range (rejection sample if blocked)
        opp_hands_str: list[list[str]] = []
        sim_used = set(used)
        ok = True
        for _opp in range(num_opponents):
            attempts = 0
            while True:
                attempts += 1
                if attempts > 50:
                    ok = False
                    break
                a, b = rng.choice(opp_combos)
                if a not in sim_used and b not in sim_used:
                    opp_hands_str.append([a, b])
                    sim_used.add(a)
                    sim_used.add(b)
                    break
            if not ok:
                break
        if not ok:
            continue

        # Complete the board from remaining deck
        remaining = [c for c in _DECK if c not in sim_used]
        rng.shuffle(remaining)
        runout = remaining[:cards_to_complete_board]
        if len(runout) < cards_to_complete_board:
            continue
        full_board = _parse(list(board) + runout)

        my_hand = _hand_rank(my_parsed, full_board)
        opp_hands = [_hand_rank(_parse(h), full_board) for h in opp_hands_str]
        best_opp = max(opp_hands)

        if my_hand > best_opp:
            wins += 1
        elif my_hand == best_opp:
            tied_count = sum(1 for h in opp_hands if h == my_hand) + 1
            ties += 1.0 / tied_count
        actual_sims += 1

    if actual_sims == 0:
        return {"error": "no simulations completed"}

    win_rate = wins / actual_sims
    tie_rate = ties / actual_sims
    return {
        "equity": round(win_rate + tie_rate, 4),
        "win": round(win_rate, 4),
        "tie": round(tie_rate, 4),
        "simulations_run": actual_sims,
        "opponent_range": opponent_range,
        "opponent_combos": len(opp_combos),
        "range_density": round(len(opp_combos) / 1326.0, 3),
        "method": "monte-carlo",
    }
