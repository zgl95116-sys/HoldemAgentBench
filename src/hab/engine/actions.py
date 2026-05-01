"""Action and LegalAction models, plus a validate_action() helper."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ActionType = Literal["fold", "check", "call", "bet", "raise", "all_in"]


class LegalAction(BaseModel):
    type: ActionType
    amount: float | None = None        # for call: the increment to call
    amount_min: float | None = None    # for raise/bet: absolute "raise to" lower bound
    amount_max: float | None = None    # for raise/bet: absolute "raise to" upper bound


class Action(BaseModel):
    action: ActionType
    amount: float | None = None
    reason: str | None = None
    tool_calls_used: list[str] = Field(default_factory=list)
    timestamp: str | None = None
    hand_id: str | None = None


def validate_action(action: Action, legal: list[LegalAction]) -> str | None:
    """Return None if valid, otherwise an error string describing the violation."""
    matching = [la for la in legal if la.type == action.action]
    if not matching:
        return f"action type '{action.action}' not legal; legal: {[la.type for la in legal]}"
    la = matching[0]
    if la.type in ("fold", "check"):
        return None
    if la.type == "call":
        if (
            action.amount is not None
            and la.amount is not None
            and abs(action.amount - la.amount) > 1e-6
        ):
            return f"call amount {action.amount} does not match required {la.amount}"
        return None
    if la.type in ("bet", "raise", "all_in"):
        if action.amount is None:
            return f"{la.type} requires amount"
        if la.amount_min is not None and action.amount < la.amount_min - 1e-6:
            return f"{la.type} amount {action.amount} below min {la.amount_min}"
        if la.amount_max is not None and action.amount > la.amount_max + 1e-6:
            return f"{la.type} amount {action.amount} above max {la.amount_max}"
        return None
    return None
