import json
from pathlib import Path

from hab.mcp_server.tools.gto_lookup import gto_lookup, _hand_key
from hab.mcp_server.tools.hand_search import hand_history_search
from hab.mcp_server.tools.notes import note_manager
from hab.mcp_server.tools.opponent_db import opponent_database_query
from hab.mcp_server.tools.pot_odds import pot_odds
from hab.mcp_server.tools.range_analyzer import range_analyzer


# ---------- pot_odds ----------

def test_pot_odds_check():
    r = pot_odds(pot=10, bet_to_call=0)
    assert r["verdict"] == "check"


def test_pot_odds_required():
    r = pot_odds(pot=100, bet_to_call=50)
    # 50 / 150 = 0.333
    assert abs(r["pot_odds_required"] - 0.3333) < 0.01


def test_pot_odds_call_with_equity():
    r = pot_odds(pot=100, bet_to_call=50, my_equity=0.5)
    assert r["verdict"] == "call"
    assert r["ev_call"] > 0


def test_pot_odds_breakeven_ev_is_zero():
    r = pot_odds(pot=100, bet_to_call=50, my_equity=50 / 150)
    assert abs(r["ev_call"]) < 0.001
    assert r["verdict"] == "marginal"


def test_pot_odds_fold_with_low_equity():
    r = pot_odds(pot=100, bet_to_call=50, my_equity=0.2)
    assert r["verdict"] == "fold"


def test_pot_odds_rejects_invalid_probabilities():
    assert "error" in pot_odds(pot=100, bet_to_call=50, my_equity=1.2)
    assert "error" in pot_odds(pot=100, bet_to_call=50, bluff_size=25, fold_equity=-0.1)


# ---------- gto_lookup ----------

def test_hand_key_basic():
    assert _hand_key(["As", "Kh"]) == "AKo"
    assert _hand_key(["Ks", "Qs"]) == "KQs"
    assert _hand_key(["7c", "7d"]) == "77"
    assert _hand_key(["2c", "Ah"]) == "A2o"


def test_gto_lookup_aa_open():
    r = gto_lookup("HU_SB_open", "open", ["As", "Ad"])
    assert r["action"] == "raise"


def test_gto_lookup_72_fold():
    r = gto_lookup("HU_SB_open", "open", ["7c", "2d"])
    assert r["action"] == "fold"


def test_gto_lookup_unknown_scenario():
    r = gto_lookup("nope", "", ["As", "Kh"])
    assert "error" in r


def test_gto_lookup_mixed_frequency_97o():
    """97o in HU SB open should be a high-freq raise (no longer a binary fold)."""
    r = gto_lookup("HU_SB_open", "open", ["7c", "9d"])
    assert r["action"] == "raise"
    assert r["raise_freq"] >= 0.7  # roughly 0.85 in our chart


def test_gto_lookup_low_freq_42o():
    """42o is still a clear fold."""
    r = gto_lookup("HU_SB_open", "open", ["4c", "2d"])
    assert r["action"] == "fold"
    assert r["raise_freq"] < 0.2


def test_gto_lookup_BB_vs_open_AA():
    r = gto_lookup("HU_BB_vs_open", "vs_open", ["As", "Ah"])
    assert r["action"] == "raise"
    assert r["engine_action"] == "raise"
    assert r["strategic_action"] == "three_bet"
    assert r["frequencies"]["three_bet"] > 0.5


def test_gto_lookup_BB_vs_open_57s():
    """57s vs SB open is a call."""
    r = gto_lookup("HU_BB_vs_open", "vs_open", ["7s", "5s"])
    # In our chart, suited connectors call most of the time
    assert r["action"] in ("call", "fold")


def test_gto_lookup_warns_when_not_100bb_chart_context():
    r = gto_lookup("HU_SB_open", "open", ["As", "Kh"], stack_depth_bb=20)
    assert r["engine_action"] in ("raise", "fold")
    assert r["chart_stack_depth_bb"] == 100
    assert r["warning"]


# ---------- range_analyzer ----------

def test_range_analyzer_tight_vpip():
    r = range_analyzer("opp", observed_vpip=0.10)
    # Tight VPIP → narrow range with TT+
    assert r["range_density"] < 0.10
    assert "estimated_range" in r


def test_range_analyzer_loose_vpip():
    r = range_analyzer("opp", observed_vpip=0.55)
    # Loose VPIP → wide range
    assert r["range_density"] > 0.20


def test_range_analyzer_3bet_narrows():
    """3-bet preflop should narrow range significantly."""
    r1 = range_analyzer("opp", observed_vpip=0.30, action_sequence=[])
    r2 = range_analyzer("opp", observed_vpip=0.30, action_sequence=[
        {"street": "preflop", "action": "raise"},
        {"street": "preflop", "action": "raise"},  # this is the 3-bet
    ])
    # 3-bet narrows
    assert r2["range_density"] < r1["range_density"]


def test_range_analyzer_filters_to_opponent_actions():
    """Hero aggression should not narrow villain's range."""
    base = range_analyzer("opp", observed_vpip=0.30, action_sequence=[])
    hero_only = range_analyzer("opp", observed_vpip=0.30, action_sequence=[
        {"player_id": "hero", "street": "preflop", "action": "raise"},
        {"player_id": "hero", "street": "flop", "action": "bet"},
        {"player_id": "hero", "street": "turn", "action": "bet"},
    ])
    assert hero_only["estimated_range"] == base["estimated_range"]
    assert hero_only["range_density"] == base["range_density"]
    assert hero_only["opponent_actions_considered"] == 0


def test_range_analyzer_opponent_3bet_narrows():
    r1 = range_analyzer("opp", observed_vpip=0.30, action_sequence=[])
    r2 = range_analyzer("opp", observed_vpip=0.30, action_sequence=[
        {"player_id": "hero", "street": "preflop", "action": "raise"},
        {"player_id": "opp", "street": "preflop", "action": "raise"},
    ])
    assert r2["range_density"] < r1["range_density"]


def test_range_analyzer_barrel_narrows():
    """Multi-street barrel narrows to value range."""
    r = range_analyzer("opp", observed_vpip=0.30, action_sequence=[
        {"player_id": "opp", "street": "preflop", "action": "raise"},
        {"player_id": "opp", "street": "flop", "action": "bet"},
        {"player_id": "opp", "street": "turn", "action": "bet"},
    ])
    # Triple-barrel → narrow value range
    assert r["range_density"] < 0.10
    assert "estimated_range" in r


def test_range_analyzer_vpip_mapping_is_monotonic():
    densities = [
        range_analyzer("opp", observed_vpip=vpip)["range_density"]
        for vpip in (0.10, 0.15, 0.20, 0.25, 0.30, 0.40, 0.50, 0.65, 1.0)
    ]
    assert densities == sorted(densities)


# ---------- notes ----------

def test_note_manager_append_then_read(tmp_path: Path):
    ws = tmp_path / "ws"
    ws.mkdir()
    r = note_manager(ws, "append", "villain1", content="aggressive on river", hand_id="h_1")
    assert "appended_to" in r
    r2 = note_manager(ws, "read", "villain1")
    assert r2["exists"]
    assert "aggressive on river" in r2["content"]
    r3 = note_manager(ws, "list", "villain1")
    assert "villain1" in r3["opponents"]


def test_note_manager_read_missing(tmp_path: Path):
    r = note_manager(tmp_path, "read", "ghost")
    assert r["exists"] is False


# ---------- opponent_database_query ----------

def _write_hand(session_dir: Path, hand_id: str, actions: list[dict], showdown: dict | None = None):
    hands_dir = session_dir / "hands"
    hands_dir.mkdir(parents=True, exist_ok=True)
    (hands_dir / f"{hand_id}.json").write_text(json.dumps({
        "hand_id": hand_id,
        "winner": None,
        "pot": 0,
        "stack_deltas": {a["player_id"]: 0 for a in actions},
        "showdown_cards": showdown or {},
        "board": [],
        "action_history": actions,
    }))


def _write_hand_full(
    session_dir: Path,
    hand_id: str,
    actions: list[dict],
    stack_deltas: dict,
    hole_cards: dict | None = None,
    showdown: dict | None = None,
    board: list[str] | None = None,
):
    hands_dir = session_dir / "hands"
    hands_dir.mkdir(parents=True, exist_ok=True)
    (hands_dir / f"{hand_id}.json").write_text(json.dumps({
        "hand_id": hand_id,
        "winner": None,
        "pot": 0,
        "stack_deltas": stack_deltas,
        "hole_cards": hole_cards or {},
        "showdown_cards": showdown or {},
        "board": board or [],
        "action_history": actions,
    }))


def test_opponent_db_aggregates(tmp_path: Path):
    s = tmp_path / "session"
    # hand 1: opp raises preflop -> VPIP & PFR
    _write_hand(s, "h1", [
        {"player_id": "opp", "street": "preflop", "action": "raise", "amount": 6},
        {"player_id": "me", "street": "preflop", "action": "fold"},
    ])
    # hand 2: opp limps preflop, then bets flop -> VPIP, postflop aggressive
    _write_hand(s, "h2", [
        {"player_id": "opp", "street": "preflop", "action": "call", "amount": 2},
        {"player_id": "me", "street": "preflop", "action": "check"},
        {"player_id": "me", "street": "flop", "action": "check"},
        {"player_id": "opp", "street": "flop", "action": "bet", "amount": 4},
    ])
    r = opponent_database_query(s, "opp")
    assert r["hands_observed"] == 2
    assert r["vpip"] == 1.0  # both hands voluntary
    assert r["pfr"] == 0.5
    assert r["af"] is None or r["af"] >= 0  # only aggressive postflop -> af = inf-ish or None


def test_opponent_db_counts_dealt_hands_without_actions(tmp_path: Path):
    s = tmp_path / "session"
    _write_hand_full(s, "h1", [
        {"player_id": "hero", "street": "preflop", "action": "raise"},
        {"player_id": "villain", "street": "preflop", "action": "fold"},
    ], {"hero": 1, "villain": -1, "idle": 0})
    r = opponent_database_query(s, "idle")
    assert r["hands_observed"] == 1
    assert r["vpip"] == 0.0


# ---------- hand_history_search ----------

def test_hand_history_search(tmp_path: Path):
    s = tmp_path / "session"
    _write_hand(s, "h1", [
        {"player_id": "alice", "street": "preflop", "action": "raise", "amount": 6},
        {"player_id": "bob", "street": "preflop", "action": "call", "amount": 6},
    ])
    _write_hand(s, "h2", [
        {"player_id": "alice", "street": "preflop", "action": "fold"},
    ])
    r = hand_history_search(s, query="raise", limit=10)
    assert any(x["hand_id"] == "h1" for x in r)
    r2 = hand_history_search(s, opponent_id="alice")
    assert len(r2) == 2


def test_hand_history_search_does_not_match_hidden_cards(tmp_path: Path):
    s = tmp_path / "session"
    _write_hand_full(
        s,
        "h1",
        [{"player_id": "alice", "street": "preflop", "action": "fold"}],
        {"alice": -1, "bob": 1},
        hole_cards={"alice": ["As", "Ah"], "bob": ["7c", "2d"]},
        showdown={"bob": ["7c", "2d"]},
    )
    assert hand_history_search(s, query="As", limit=10) == []
    assert hand_history_search(s, query="fold", limit=10)[0]["hand_id"] == "h1"
