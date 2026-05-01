"""Game master: thin orchestration over pokerkit + event stream.

Supports N players (HU, 6-max, etc.). Seat mapping uses pokerkit's convention:
seat[i] = players[(button_idx + 1 + i) % N]
- HU (N=2): seat 0 = BB (non-button), seat 1 = button = SB
- N>=3: seat 0 = SB, seat 1 = BB, seat N-1 = button

The button rotates one position per hand (clockwise).
"""
from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from pokerkit import Automation, NoLimitTexasHoldem, State
from pydantic import BaseModel, ConfigDict

from hab.engine.actions import Action, LegalAction, validate_action
from hab.engine.state import (
    ActionHistoryEntry,
    GameView,
    HandResult,
    HoleCards,
    Stacks,
)

_AUTOMATIONS = (
    Automation.ANTE_POSTING,
    Automation.BET_COLLECTION,
    Automation.BLIND_OR_STRADDLE_POSTING,
    Automation.CARD_BURNING,
    Automation.HOLE_DEALING,
    Automation.BOARD_DEALING,
    Automation.RUNOUT_COUNT_SELECTION,
    Automation.HOLE_CARDS_SHOWING_OR_MUCKING,
    Automation.HAND_KILLING,
    Automation.CHIPS_PUSHING,
    Automation.CHIPS_PULLING,
)

_STREET_NAMES = {0: "preflop", 1: "flop", 2: "turn", 3: "river"}


@dataclass
class GameMasterConfig:
    players: list[str]
    small_blind: float = 1.0
    big_blind: float = 2.0
    starting_stack: float = 200.0
    hands_target: int = 100
    seed: int | None = None
    decision_timeout_sec: float = 300.0
    duplicate_templates: bool = False


class Event(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    type: str
    hand_id: str | None = None
    player_id: str | None = None
    game_view: GameView | None = None
    hole_cards: HoleCards | None = None
    legal_actions: list[LegalAction] = []
    payload: dict[str, Any] = {}


def _format_card(c) -> str:
    return f"{c.rank.value}{c.suit.value}"


class GameMaster:
    def __init__(self, config: GameMasterConfig):
        if len(config.players) < 2:
            raise ValueError("Need at least 2 players")
        self.config = config
        self.rng = random.Random(config.seed)
        self.stacks: dict[str, float] = {p: float(config.starting_stack) for p in config.players}
        self._action_received = asyncio.Event()
        self._submitted_action: Action | None = None
        self._submitting_player: str | None = None
        self._hand_counter = 0
        self.history: list[HandResult] = []
        self._button_player_idx = 0

    @property
    def n_players(self) -> int:
        return len(self.config.players)

    def _seat_to_player(self) -> list[str]:
        """seat[i] = players[(button_idx + 1 + i) % N]"""
        n = self.n_players
        return [
            self.config.players[(self._button_player_idx + 1 + i) % n] for i in range(n)
        ]

    async def submit_action(self, player_id: str, action: Action) -> None:
        if self._submitting_player and player_id != self._submitting_player:
            raise RuntimeError(
                f"action from {player_id} but {self._submitting_player} is to act"
            )
        self._submitted_action = action
        self._action_received.set()

    async def events(self) -> AsyncIterator[Event]:
        yield Event(type="session_start", payload={"players": list(self.config.players)})

        for _ in range(self.config.hands_target):
            # Stop when fewer than 2 players have chips
            if self.config.duplicate_templates:
                with_chips = list(self.config.players)
            else:
                with_chips = [p for p, s in self.stacks.items() if s > 0]
            if len(with_chips) < 2:
                break
            self._hand_counter += 1
            hand_id = f"h_{self._hand_counter:05d}"
            duplicate_template_id, duplicate_rotation, seed_index = self._hand_plan()
            async for ev in self._run_hand(
                hand_id,
                duplicate_template_id=duplicate_template_id,
                duplicate_rotation=duplicate_rotation,
                seed_index=seed_index,
            ):
                yield ev
            self._button_player_idx = (self._button_player_idx + 1) % self.n_players

        yield Event(
            type="session_complete",
            payload={
                "final_stacks": dict(self.stacks),
                "hands_played": self._hand_counter,
                "history": [h.model_dump() for h in self.history],
            },
        )

    def _hand_plan(self) -> tuple[str | None, int | None, int]:
        if not self.config.duplicate_templates:
            return None, None, self._hand_counter

        rotations_per_template = max(1, self.n_players)
        template_index = (self._hand_counter - 1) // rotations_per_template + 1
        rotation = (self._hand_counter - 1) % rotations_per_template
        return f"t_{template_index:05d}", rotation, template_index

    async def _run_hand(
        self,
        hand_id: str,
        *,
        duplicate_template_id: str | None = None,
        duplicate_rotation: int | None = None,
        seed_index: int | None = None,
    ) -> AsyncIterator[Event]:
        cfg = self.config
        # Skip players with no chips (sit them out for this hand)
        full_seats = self._seat_to_player()
        if cfg.duplicate_templates:
            active_seats = full_seats
        else:
            active_seats = [p for p in full_seats if self.stacks[p] > 0]
        if len(active_seats) < 2:
            return
        seat_to_player = active_seats
        n_active = len(seat_to_player)
        if cfg.duplicate_templates:
            seat_stacks = [int(cfg.starting_stack) for _ in seat_to_player]
            starting_stacks_snapshot = {p: float(cfg.starting_stack) for p in cfg.players}
        else:
            seat_stacks = [int(self.stacks[p]) for p in seat_to_player]
            starting_stacks_snapshot = {p: float(self.stacks[p]) for p in cfg.players}

        # Seed the global RNG that pokerkit uses, so card dealing is deterministic
        # given the master seed + hand plan. Duplicate mode intentionally reuses
        # the same seed across a full table rotation.
        if cfg.seed is not None:
            hand_seed = cfg.seed * 1_000_003 + (seed_index or self._hand_counter)
            random.seed(hand_seed)

        state: State = NoLimitTexasHoldem.create_state(
            automations=_AUTOMATIONS,
            ante_trimming_status=True,
            raw_antes=0,
            raw_blinds_or_straddles=(int(cfg.small_blind), int(cfg.big_blind)),
            min_bet=int(cfg.big_blind),
            raw_starting_stacks=seat_stacks,
            player_count=n_active,
        )

        action_history: list[ActionHistoryEntry] = []

        # Button is the player at seat N-1 in pokerkit's convention; for HU, seat 1.
        button_player = seat_to_player[-1] if n_active > 2 else seat_to_player[1]
        max_pot_observed = float(state.total_pot_amount or 0)

        yield Event(
            type="hand_start",
            hand_id=hand_id,
            payload={
                "button": button_player,
                "seat_to_player": seat_to_player,
                "n_active": n_active,
                "duplicate_template_id": duplicate_template_id,
                "duplicate_rotation": duplicate_rotation,
            },
        )

        while state.status:
            if state.actor_index is None:
                await asyncio.sleep(0)
                continue
            actor_seat = state.actor_index
            actor = seat_to_player[actor_seat]
            view = self._build_view(hand_id, seat_to_player, state, action_history)
            hole_pkit = state.hole_cards[actor_seat] if actor_seat < len(state.hole_cards) else []
            hc = HoleCards(hand_id=hand_id, cards=[_format_card(c) for c in hole_pkit])

            self._action_received.clear()
            self._submitted_action = None
            self._submitting_player = actor

            yield Event(
                type="action_needed",
                hand_id=hand_id,
                player_id=actor,
                game_view=view,
                hole_cards=hc,
                legal_actions=view.legal_actions,
            )

            try:
                await asyncio.wait_for(
                    self._action_received.wait(),
                    timeout=cfg.decision_timeout_sec,
                )
            except asyncio.TimeoutError:
                self._submitted_action = Action(action="fold", hand_id=hand_id, reason="timeout")

            action = self._submitted_action or Action(
                action="fold", hand_id=hand_id, reason="no_action"
            )
            err = validate_action(action, view.legal_actions)
            if err:
                # Try to convert to a legal fallback (check if check is legal, else fold)
                legal_types = {la.type for la in view.legal_actions}
                if "fold" in legal_types:
                    action = Action(
                        action="fold", hand_id=hand_id, reason=f"invalid_action:{err}"
                    )
                elif "check" in legal_types:
                    action = Action(
                        action="check", hand_id=hand_id, reason=f"invalid_action_fallback:{err}"
                    )

            max_pot_observed = max(max_pot_observed, float(view.pot))
            stack_before = float(state.stacks[actor_seat])
            try:
                self._apply_action(state, action)
            except Exception as e:
                if state.can_fold():
                    state.fold()
                    action = Action(action="fold", hand_id=hand_id, reason=f"engine_rejected:{e}")
                elif state.can_check_or_call():
                    state.check_or_call()
                    action = Action(action="check", hand_id=hand_id, reason=f"engine_rejected:{e}")
            stack_after = float(state.stacks[actor_seat])
            contributed = max(0.0, stack_before - stack_after)
            max_pot_observed = max(
                max_pot_observed,
                float(view.pot) + contributed,
                float(state.total_pot_amount or 0),
            )

            action_history.append(
                ActionHistoryEntry(
                    player_id=actor,
                    street=view.street,
                    action=action.action,
                    amount=action.amount,
                    reason=action.reason,
                    tool_calls_used=list(action.tool_calls_used or []),
                    pot_before=view.pot,
                )
            )

        # Hand complete
        new_stacks = {seat_to_player[i]: float(state.stacks[i]) for i in range(n_active)}
        if cfg.duplicate_templates:
            deltas = {
                p: new_stacks.get(p, cfg.starting_stack) - cfg.starting_stack
                for p in cfg.players
            }
            for p in cfg.players:
                self.stacks[p] += deltas[p]
        else:
            # Players sat out keep their stacks unchanged
            deltas = {
                p: new_stacks.get(p, self.stacks[p]) - self.stacks[p]
                for p in cfg.players
            }
            for p in cfg.players:
                self.stacks[p] = new_stacks.get(p, self.stacks[p])

        winner = max(deltas, key=lambda k: deltas[k]) if deltas else None
        board: list[str] = []
        for street_cards in (state.board_cards or []):
            board.extend(_format_card(c) for c in street_cards)
        reached_showdown = (
            len(board) == 5
            and not any(a.action == "fold" for a in action_history)
        )
        showdown: dict[str, list[str]] = {}
        if reached_showdown:
            try:
                for seat_idx, hole in enumerate(state.hole_cards):
                    if hole:
                        showdown[seat_to_player[seat_idx]] = [_format_card(c) for c in hole]
            except Exception:
                pass

        result = HandResult(
            hand_id=hand_id,
            winner=winner,
            pot=max_pot_observed,
            stack_deltas=deltas,
            showdown_cards=showdown,
            hole_cards=showdown,
            board=board,
            action_history=action_history,
            starting_stacks=starting_stacks_snapshot,
            button=button_player,
            duplicate_template_id=duplicate_template_id,
            duplicate_rotation=duplicate_rotation,
        )
        self.history.append(result)
        yield Event(type="hand_complete", hand_id=hand_id, payload=result.model_dump())

    def _build_view(
        self,
        hand_id: str,
        seat_to_player: list[str],
        state: State,
        history: list[ActionHistoryEntry],
    ) -> GameView:
        si = state.street_index
        if si is None:
            street_name = "complete"
        else:
            street_name = _STREET_NAMES.get(si, "preflop")

        board: list[str] = []
        for street_cards in (state.board_cards or []):
            board.extend(_format_card(c) for c in street_cards)

        legal: list[LegalAction] = []
        if state.can_fold():
            legal.append(LegalAction(type="fold"))
        if state.can_check_or_call():
            call_amt = float(state.checking_or_calling_amount or 0)
            if call_amt == 0:
                legal.append(LegalAction(type="check"))
            else:
                legal.append(LegalAction(type="call", amount=call_amt))
        if state.can_complete_bet_or_raise_to():
            min_amt = float(state.min_completion_betting_or_raising_to_amount or 0)
            max_amt = float(state.max_completion_betting_or_raising_to_amount or 0)
            legal.append(LegalAction(type="raise", amount_min=min_amt, amount_max=max_amt))

        stack_dict = {seat_to_player[i]: float(state.stacks[i]) for i in range(len(seat_to_player))}
        actor = seat_to_player[state.actor_index] if state.actor_index is not None else None
        current_bet = float(state.checking_or_calling_amount or 0)

        return GameView(
            hand_id=hand_id,
            table_id="table_1",
            street=street_name,
            board=board,
            pot=float(state.total_pot_amount),
            to_act=actor,
            stacks=Stacks(root=stack_dict),
            current_bet=current_bet,
            action_history=history,
            legal_actions=legal,
            big_blind=self.config.big_blind,
            small_blind=self.config.small_blind,
            deadline=datetime.now(timezone.utc).isoformat(),
        )

    def _apply_action(self, state: State, action: Action) -> None:
        if action.action == "fold":
            state.fold()
        elif action.action in ("check", "call"):
            state.check_or_call()
        elif action.action in ("bet", "raise", "all_in"):
            amt = action.amount or 0
            mn = state.min_completion_betting_or_raising_to_amount
            mx = state.max_completion_betting_or_raising_to_amount
            if mn is not None and amt < mn:
                amt = mn
            if mx is not None and amt > mx:
                amt = mx
            state.complete_bet_or_raise_to(int(amt))
