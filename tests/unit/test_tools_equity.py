"""Equity calculator sanity checks. Tolerances are loose because Monte Carlo
has variance; we mainly verify directionality and edge cases."""
from hab.mcp_server.tools.equity import equity


def test_aces_vs_random_high_equity():
    r = equity(["As", "Ah"], simulations=2000, seed=1)
    assert r["equity"] > 0.8
    assert r["simulations_run"] >= 1500


def test_72_off_vs_random_low_equity():
    r = equity(["7c", "2d"], simulations=2000, seed=1)
    assert r["equity"] < 0.45


def test_set_on_dry_board_dominates():
    # 7c7d on 7s K2c board vs random
    r = equity(["7c", "7d"], board=["7s", "Kh", "2c"], simulations=1500, seed=2)
    assert r["equity"] > 0.85


def test_invalid_input():
    assert "error" in equity(["As"])
    assert "error" in equity(["As", "Kh"], num_opponents=0)
    assert "error" in equity(["As", "Kh"], board=["Qs"])  # incomplete board
    assert "error" in equity(["As", "As"])
    assert "error" in equity(["As", "Kh"], board=["As", "Qd", "2c"])
    assert "error" in equity(["Ax", "Kh"])


def test_invalid_range_does_not_fallback_to_random():
    r = equity(["As", "Ah"], opponent_range="garbage", simulations=200, seed=1)
    assert "error" in r
    assert r["opponent_range"] == "garbage"


def test_partially_invalid_range_errors():
    r = equity(["As", "Ah"], opponent_range="KK,garbage", simulations=200, seed=1)
    assert "error" in r


def test_equity_against_tight_range_lower_than_random():
    """AKo vs random ≈ 65%; AKo vs 'tight' (TT+,AJs+,AQo+) is much closer to 50%."""
    rand = equity(["As", "Kh"], opponent_range="random", simulations=1500, seed=1)
    tight = equity(["As", "Kh"], opponent_range="tight", simulations=1500, seed=1)
    assert rand["equity"] > tight["equity"] + 0.05
    assert tight["range_density"] < 0.10  # tight is small range


def test_equity_named_HU_SB_open():
    """BB vs SB-open range — verify named preset works."""
    r = equity(["As", "Ks"], opponent_range="HU_SB_open", simulations=1500, seed=1)
    assert "equity" in r
    assert r["range_density"] > 0.5  # HU SB open is wide
    assert r["opponent_combos"] > 700  # ~785 after blocker filter


def test_equity_explicit_range_string():
    """Custom range string should work."""
    r = equity(
        ["Ah", "Ad"],
        opponent_range="KK,QQ,JJ",
        simulations=1500,
        seed=1,
    )
    assert r["opponent_combos"] == 18  # 3 pairs × 6 combos = 18
    assert r["equity"] > 0.78  # AA dominates KK/QQ/JJ


def test_river_no_runout_runs():
    # All cards known, just direct evaluation
    r = equity(
        ["As", "Ad"],
        board=["Kh", "Kd", "Qc", "2s", "3h"],
        simulations=500,
        seed=3,
    )
    assert "equity" in r
    assert r["simulations_run"] >= 1
