from hab.engine.actions import Action, LegalAction, validate_action
from hab.engine.state import GameView, Stacks


def test_validate_fold_always_legal():
    legal = [LegalAction(type="fold")]
    assert validate_action(Action(action="fold"), legal) is None


def test_validate_call_amount_must_match():
    legal = [LegalAction(type="call", amount=40)]
    assert validate_action(Action(action="call", amount=40), legal) is None
    err = validate_action(Action(action="call", amount=20), legal)
    assert err and "amount" in err


def test_validate_raise_min_max():
    legal = [LegalAction(type="raise", amount_min=80, amount_max=480)]
    assert validate_action(Action(action="raise", amount=100), legal) is None
    assert validate_action(Action(action="raise", amount=40), legal)
    assert validate_action(Action(action="raise", amount=500), legal)


def test_validate_illegal_type():
    legal = [LegalAction(type="fold")]
    err = validate_action(Action(action="raise", amount=10), legal)
    assert err and "not legal" in err


def test_game_view_serialization_roundtrip():
    gv = GameView(
        hand_id="h_1",
        table_id="t_1",
        street="flop",
        board=["Qs", "Jh", "2c"],
        pot=120,
        to_act="player_a",
        stacks=Stacks(root={"player_a": 480, "player_b": 420}),
        current_bet=40,
        action_history=[],
        legal_actions=[LegalAction(type="fold"), LegalAction(type="call", amount=40)],
    )
    blob = gv.model_dump_json()
    gv2 = GameView.model_validate_json(blob)
    assert gv2.hand_id == "h_1"
    assert gv2.legal_actions[1].type == "call"
    assert gv2.legal_actions[1].amount == 40
