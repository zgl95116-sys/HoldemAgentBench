import asyncio

import pytest

from hab.engine.actions import Action
from hab.engine.game_master import GameMaster, GameMasterConfig


@pytest.mark.asyncio
async def test_six_handed_one_hand_runs():
    cfg = GameMasterConfig(
        players=[f"p{i}" for i in range(6)],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=1,
        seed=42,
    )
    gm = GameMaster(cfg)

    async def play():
        result = None
        async for event in gm.events():
            if event.type == "action_needed":
                # Always fold first; if fold not legal (e.g. BB facing free check), check
                legal_types = {la.type for la in event.legal_actions}
                if "fold" in legal_types:
                    a = Action(action="fold", hand_id=event.hand_id)
                else:
                    a = Action(action="check", hand_id=event.hand_id)
                await gm.submit_action(event.player_id, a)
            elif event.type == "session_complete":
                result = event.payload
        return result

    result = await asyncio.wait_for(play(), timeout=10.0)
    assert result is not None
    assert sum(result["final_stacks"].values()) == pytest.approx(1200.0)


@pytest.mark.asyncio
async def test_six_handed_button_rotates():
    cfg = GameMasterConfig(
        players=[f"p{i}" for i in range(6)],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=6,
        seed=1,
    )
    gm = GameMaster(cfg)
    buttons: list[str] = []

    async def play():
        async for event in gm.events():
            if event.type == "hand_start":
                buttons.append(event.payload["button"])
            elif event.type == "action_needed":
                legal_types = {la.type for la in event.legal_actions}
                a = Action(
                    action="fold" if "fold" in legal_types else "check",
                    hand_id=event.hand_id,
                )
                await gm.submit_action(event.player_id, a)

    await asyncio.wait_for(play(), timeout=10.0)
    # Each of 6 players should be button at least once
    assert len(set(buttons)) == 6


@pytest.mark.asyncio
async def test_three_handed_to_completion():
    cfg = GameMasterConfig(
        players=["p0", "p1", "p2"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=50.0,  # small stacks → quicker bust-out
        hands_target=200,
        seed=7,
    )
    gm = GameMaster(cfg)

    async def play():
        result = None
        async for event in gm.events():
            if event.type == "action_needed":
                legal_types = {la.type for la in event.legal_actions}
                # Mix: prefer raise to provoke action
                if "raise" in legal_types:
                    raise_la = next(la for la in event.legal_actions if la.type == "raise")
                    a = Action(action="raise", amount=raise_la.amount_min, hand_id=event.hand_id)
                elif "check" in legal_types:
                    a = Action(action="check", hand_id=event.hand_id)
                elif "call" in legal_types:
                    call_la = next(la for la in event.legal_actions if la.type == "call")
                    a = Action(action="call", amount=call_la.amount, hand_id=event.hand_id)
                else:
                    a = Action(action="fold", hand_id=event.hand_id)
                await gm.submit_action(event.player_id, a)
            elif event.type == "session_complete":
                result = event.payload
        return result

    result = await asyncio.wait_for(play(), timeout=30.0)
    assert result is not None
    # Total chips conserved
    assert sum(result["final_stacks"].values()) == pytest.approx(150.0)
