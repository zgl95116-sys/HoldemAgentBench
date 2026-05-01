"""Pot-odds, EV, and bluff-EV math."""
from __future__ import annotations


def pot_odds(
    pot: float,
    bet_to_call: float,
    my_equity: float | None = None,
    bluff_size: float | None = None,
    fold_equity: float | None = None,
) -> dict:
    """Returns pot-odds analysis.

    For CALLING:
      pot_odds_required: equity needed to break even on call
      ev_call: expected value of calling (if my_equity given)
      verdict: "call" / "fold" / "marginal"

    For BLUFFING (if bluff_size + fold_equity given):
      bluff_breakeven: fold_equity needed to break even on the bluff
      ev_bluff: expected value of bluff (using my_equity for if-called scenario)
    """
    if bet_to_call < 0 or pot < 0:
        return {"error": "pot and bet_to_call must be >= 0"}
    if my_equity is not None and not 0 <= my_equity <= 1:
        return {"error": "my_equity must be between 0 and 1"}
    if bluff_size is not None and bluff_size < 0:
        return {"error": "bluff_size must be >= 0"}
    if fold_equity is not None and not 0 <= fold_equity <= 1:
        return {"error": "fold_equity must be between 0 and 1"}

    out: dict = {}

    # === Calling math ===
    if bet_to_call == 0:
        out["verdict"] = "check"
        out["pot_odds_required"] = 0.0
        out["ratio"] = "free check"
    else:
        required = bet_to_call / (pot + bet_to_call)
        out["pot_odds_required"] = round(required, 4)
        out["ratio"] = f"{pot:.1f}:{bet_to_call:.1f}"
        if my_equity is not None:
            # Net EV relative to folding now: win the current pot, or lose the call.
            ev_call = my_equity * pot - (1 - my_equity) * bet_to_call
            out["ev_call"] = round(ev_call, 4)
            margin = my_equity - required
            out["equity_margin"] = round(margin, 4)
            if margin > 0.05:
                out["verdict"] = "call"
            elif margin < -0.02:
                out["verdict"] = "fold"
            else:
                out["verdict"] = "marginal"

    # === Bluffing math ===
    if bluff_size is not None and bluff_size > 0:
        bluff_breakeven = bluff_size / (pot + bluff_size)
        out["bluff_breakeven_fold_equity"] = round(bluff_breakeven, 4)
        if fold_equity is not None:
            # EV of betting bluff_size:
            # If villain folds (prob = fold_equity): we win the pot
            # If villain calls (prob = 1 - fold_equity): we play out at my_equity
            # If we don't have my_equity, assume 0 (pure bluff)
            eq = my_equity if my_equity is not None else 0.0
            ev_bluff = (
                fold_equity * pot
                + (1 - fold_equity) * (eq * (pot + 2 * bluff_size) - bluff_size)
            )
            out["ev_bluff"] = round(ev_bluff, 4)
            out["bluff_verdict"] = (
                "bluff"
                if fold_equity > bluff_breakeven + 0.05
                else ("marginal" if abs(fold_equity - bluff_breakeven) < 0.05 else "give-up")
            )

    return out
