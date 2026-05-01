import asyncio

import pytest

from hab.engine.actions import Action
from hab.engine.game_master import GameMaster, GameMasterConfig


@pytest.mark.asyncio
async def test_hu_one_hand_runs():
    cfg = GameMasterConfig(
        players=["player_a", "player_b"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=1,
        seed=42,
    )
    gm = GameMaster(cfg)

    async def play():
        result_payload = None
        async for event in gm.events():
            if event.type == "action_needed":
                await gm.submit_action(
                    event.player_id, Action(action="fold", hand_id=event.hand_id)
                )
            elif event.type == "session_complete":
                result_payload = event.payload
        return result_payload

    result = await asyncio.wait_for(play(), timeout=5.0)
    assert result is not None
    assert "final_stacks" in result
    assert sum(result["final_stacks"].values()) == pytest.approx(400.0)


@pytest.mark.asyncio
async def test_legal_actions_present_at_first_decision():
    cfg = GameMasterConfig(
        players=["a", "b"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=1,
        seed=1,
    )
    gm = GameMaster(cfg)
    seen_legal = []

    async def play():
        async for event in gm.events():
            if event.type == "action_needed":
                seen_legal.append(event.legal_actions)
                await gm.submit_action(
                    event.player_id, Action(action="fold", hand_id=event.hand_id)
                )
            elif event.type == "session_complete":
                return

    await asyncio.wait_for(play(), timeout=5.0)
    assert len(seen_legal) >= 1
    types = {la.type for la in seen_legal[0]}
    assert "fold" in types


@pytest.mark.asyncio
async def test_folded_hand_records_public_pot_without_hidden_cards():
    cfg = GameMasterConfig(
        players=["a", "b"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=1,
        seed=42,
    )
    gm = GameMaster(cfg)
    hand = None

    async def play():
        nonlocal hand
        async for event in gm.events():
            if event.type == "action_needed":
                await gm.submit_action(event.player_id, Action(action="fold", hand_id=event.hand_id))
            elif event.type == "hand_complete":
                hand = event.payload

    await asyncio.wait_for(play(), timeout=5.0)
    assert hand is not None
    assert hand["pot"] == pytest.approx(3.0)
    assert hand["hole_cards"] == {}
    assert hand["showdown_cards"] == {}


@pytest.mark.asyncio
async def test_called_blinds_record_full_pot():
    cfg = GameMasterConfig(
        players=["a", "b"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=1,
        seed=99,
    )
    gm = GameMaster(cfg)
    hand = None

    async def play():
        nonlocal hand
        async for event in gm.events():
            if event.type == "action_needed":
                legal_types = {la.type for la in event.legal_actions}
                if "check" in legal_types:
                    action = Action(action="check", hand_id=event.hand_id)
                elif "call" in legal_types:
                    call_la = next(la for la in event.legal_actions if la.type == "call")
                    action = Action(action="call", amount=call_la.amount, hand_id=event.hand_id)
                else:
                    action = Action(action="fold", hand_id=event.hand_id)
                await gm.submit_action(event.player_id, action)
            elif event.type == "hand_complete":
                hand = event.payload

    await asyncio.wait_for(play(), timeout=5.0)
    assert hand is not None
    assert hand["pot"] >= 4.0


@pytest.mark.asyncio
async def test_button_rotates_each_hand():
    cfg = GameMasterConfig(
        players=["a", "b"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=4,
        seed=7,
    )
    gm = GameMaster(cfg)
    buttons: list[str] = []

    async def play():
        async for event in gm.events():
            if event.type == "hand_start":
                buttons.append(event.payload["button"])
            elif event.type == "action_needed":
                await gm.submit_action(
                    event.player_id, Action(action="fold", hand_id=event.hand_id)
                )

    await asyncio.wait_for(play(), timeout=5.0)
    # Buttons should alternate (until someone busts)
    assert len(buttons) >= 2
    assert buttons[0] != buttons[1]


@pytest.mark.asyncio
async def test_seed_makes_dealing_deterministic():
    async def collect(seed: int) -> list[tuple[str, list[str]]]:
        cfg = GameMasterConfig(
            players=["a", "b"],
            small_blind=1.0,
            big_blind=2.0,
            starting_stack=200.0,
            hands_target=3,
            seed=seed,
        )
        gm = GameMaster(cfg)
        out: list[tuple[str, list[str]]] = []
        async for ev in gm.events():
            if ev.type == "action_needed":
                out.append((ev.player_id, list(ev.hole_cards.cards)))
                await gm.submit_action(ev.player_id, Action(action="fold", hand_id=ev.hand_id))
        return out

    r1 = await asyncio.wait_for(collect(42), timeout=5.0)
    r2 = await asyncio.wait_for(collect(42), timeout=5.0)
    r3 = await asyncio.wait_for(collect(7), timeout=5.0)
    assert r1 == r2  # same seed -> same dealing
    assert r1 != r3  # different seed -> different dealing


@pytest.mark.asyncio
async def test_duplicate_templates_reuse_deck_while_rotating_seats():
    cfg = GameMasterConfig(
        players=["a", "b"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=2,
        seed=123,
        duplicate_templates=True,
    )
    gm = GameMaster(cfg)
    starts: list[dict] = []
    hole_by_hand: dict[str, dict[str, list[str]]] = {}

    async def play():
        async for event in gm.events():
            if event.type == "hand_start":
                starts.append(event.payload)
            elif event.type == "action_needed":
                hole_by_hand.setdefault(event.hand_id, {})[event.player_id] = list(
                    event.hole_cards.cards
                )
                legal_types = {la.type for la in event.legal_actions}
                if "check" in legal_types:
                    action = Action(action="check", hand_id=event.hand_id)
                elif "call" in legal_types:
                    call_la = next(la for la in event.legal_actions if la.type == "call")
                    action = Action(
                        action="call",
                        amount=call_la.amount,
                        hand_id=event.hand_id,
                    )
                else:
                    action = Action(action="fold", hand_id=event.hand_id)
                await gm.submit_action(event.player_id, action)

    await asyncio.wait_for(play(), timeout=5.0)

    assert [s["duplicate_template_id"] for s in starts] == ["t_00001", "t_00001"]
    assert [s["duplicate_rotation"] for s in starts] == [0, 1]
    assert hole_by_hand["h_00001"]["a"] == hole_by_hand["h_00002"]["b"]
    assert hole_by_hand["h_00001"]["b"] == hole_by_hand["h_00002"]["a"]
    assert gm.history[0].starting_stacks == {"a": 200.0, "b": 200.0}
    assert gm.history[1].starting_stacks == {"a": 200.0, "b": 200.0}


@pytest.mark.asyncio
async def test_call_call_check_check_flow():
    """Both players call/check through showdown — verifies street advancement."""
    cfg = GameMasterConfig(
        players=["a", "b"],
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        hands_target=1,
        seed=99,
    )
    gm = GameMaster(cfg)

    streets_seen: set[str] = set()

    async def play():
        async for event in gm.events():
            if event.type == "action_needed":
                streets_seen.add(event.game_view.street)
                # Always check or call
                legal_types = {la.type for la in event.legal_actions}
                if "check" in legal_types:
                    a = Action(action="check", hand_id=event.hand_id)
                elif "call" in legal_types:
                    call_la = next(la for la in event.legal_actions if la.type == "call")
                    a = Action(action="call", amount=call_la.amount, hand_id=event.hand_id)
                else:
                    a = Action(action="fold", hand_id=event.hand_id)
                await gm.submit_action(event.player_id, a)
            elif event.type == "session_complete":
                return

    await asyncio.wait_for(play(), timeout=5.0)
    # We should have advanced past preflop
    assert "preflop" in streets_seen
    assert {"flop", "turn", "river"} & streets_seen
