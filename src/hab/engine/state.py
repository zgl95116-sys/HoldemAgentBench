"""Game-view data structures shared with the agent via JSON files."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, RootModel

from hab.engine.actions import LegalAction

Street = Literal["preflop", "flop", "turn", "river", "showdown", "complete"]


class Stacks(RootModel[dict[str, float]]):
    pass


class ActionHistoryEntry(BaseModel):
    player_id: str
    street: Street
    action: str
    amount: float | None = None
    reason: str | None = None
    tool_calls_used: list[str] = Field(default_factory=list)
    pot_before: float | None = None


class GameView(BaseModel):
    hand_id: str
    table_id: str
    street: Street
    board: list[str]
    pot: float
    to_act: str | None
    stacks: Stacks
    current_bet: float
    action_history: list[ActionHistoryEntry]
    legal_actions: list[LegalAction]
    deadline: str | None = None
    big_blind: float = 2.0
    small_blind: float = 1.0


class HoleCards(BaseModel):
    hand_id: str
    cards: list[str]


class HandResult(BaseModel):
    hand_id: str
    winner: str | None
    pot: float
    stack_deltas: dict[str, float]
    showdown_cards: dict[str, list[str]] = Field(default_factory=dict)
    hole_cards: dict[str, list[str]] = Field(default_factory=dict)  # public only; never mucked/hidden cards
    board: list[str] = Field(default_factory=list)
    action_history: list[ActionHistoryEntry] = Field(default_factory=list)
    starting_stacks: dict[str, float] = Field(default_factory=dict)
    button: str | None = None
    duplicate_template_id: str | None = None
    duplicate_rotation: int | None = None
