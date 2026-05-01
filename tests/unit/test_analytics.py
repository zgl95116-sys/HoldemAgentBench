import math
from pathlib import Path

import numpy as np
import pytest

from hab.analytics.duplicate import DuplicatePokerAnalyzer
from hab.analytics.elo import EloSystem
from hab.analytics.leaderboard import LeaderboardGenerator
from hab.analytics.stats import (
    PlayerStats,
    aggregate_from_hands,
    bootstrap_ci,
    calculate_bb_per_100,
)


# ---------- Layer 1: bb/100 + bootstrap ----------

def test_bb_per_100_basic():
    # 100 hands, total +200 chips, BB=2 → +100 BB → +100 BB/100
    deltas = [2.0] * 100
    assert calculate_bb_per_100(deltas, big_blind=2.0) == pytest.approx(100.0)


def test_bb_per_100_empty():
    assert calculate_bb_per_100([], big_blind=2.0) == 0.0


def test_bootstrap_ci_widens_with_variance():
    rng = np.random.default_rng(0)
    low_var = list(rng.normal(loc=2.0, scale=1.0, size=200))
    high_var = list(rng.normal(loc=2.0, scale=10.0, size=200))
    _, (l1, h1) = bootstrap_ci(low_var, big_blind=2.0, n_bootstrap=1000, seed=1)
    _, (l2, h2) = bootstrap_ci(high_var, big_blind=2.0, n_bootstrap=1000, seed=1)
    assert (h2 - l2) > (h1 - l1)


def test_bootstrap_ci_small_sample_returns_inf():
    p, (low, high) = bootstrap_ci([1.0, 2.0], big_blind=2.0)
    assert math.isinf(low) and math.isinf(high)


def test_player_stats_aggregates():
    hands = [
        {"stack_deltas": {"a": 5.0, "b": -5.0}},
        {"stack_deltas": {"a": -2.0, "b": 2.0}},
    ]
    s = aggregate_from_hands(hands, big_blind=2.0)
    assert s["a"].total_chips == 3.0
    assert s["b"].total_chips == -3.0


# ---------- Layer 3: Elo ----------

def test_elo_starts_at_1500():
    elo = EloSystem()
    assert elo.get("p1") == 1500.0


def test_elo_winner_gains():
    elo = EloSystem(k_factor=32)
    elo.update_after_session({
        "a": {"bb_per_100": 20.0, "ci": (10.0, 30.0)},
        "b": {"bb_per_100": -20.0, "ci": (-30.0, -10.0)},
    })
    assert elo.get("a") > 1500
    assert elo.get("b") < 1500
    # Symmetric (zero-sum K)
    assert pytest.approx(elo.get("a") + elo.get("b"), rel=1e-6) == 3000.0


def test_elo_overlap_is_draw():
    elo = EloSystem(k_factor=32)
    elo.update_after_session({
        "a": {"bb_per_100": 5.0, "ci": (-5.0, 15.0)},
        "b": {"bb_per_100": 0.0, "ci": (-10.0, 10.0)},
    })
    # CIs overlap → draw → both stay near 1500
    assert abs(elo.get("a") - 1500.0) < 1.0
    assert abs(elo.get("b") - 1500.0) < 1.0


def test_elo_leaderboard_sorted():
    elo = EloSystem()
    elo.update_after_session({
        "a": {"bb_per_100": 20, "ci": (10, 30)},
        "b": {"bb_per_100": -20, "ci": (-30, -10)},
        "c": {"bb_per_100": 0, "ci": (-1, 1)},
    })
    board = elo.leaderboard()
    assert board[0].rating > board[-1].rating


# ---------- Layer 2: Duplicate ----------

def test_duplicate_no_skill_zero_delta():
    """When everyone gets identical chips (template avg), all deltas are zero."""
    analyzer = DuplicatePokerAnalyzer(big_blind=2.0)
    templates = [
        {"rotations": [
            {"player_chips": {"a": 10, "b": 10}},
            {"player_chips": {"a": 10, "b": 10}},
        ]}
    ] * 20
    res = analyzer.analyze(templates)
    assert abs(res["a"].skill_bb_per_100) < 1e-6


def test_duplicate_skilled_player_positive():
    analyzer = DuplicatePokerAnalyzer(big_blind=2.0)
    templates = []
    for _ in range(20):
        templates.append({"rotations": [
            {"player_chips": {"a": 30, "b": -10}},   # a wins
            {"player_chips": {"a": 25, "b": -5}},    # a still wins
        ]})
    res = analyzer.analyze(templates)
    assert res["a"].skill_bb_per_100 > 0
    assert res["b"].skill_bb_per_100 < 0


# ---------- Leaderboard generator ----------

def test_leaderboard_pipeline(tmp_path: Path):
    gen = LeaderboardGenerator()
    # 3 sessions, model 'good/m1' wins consistently against 'weak/m2'
    rng = np.random.default_rng(0)
    for _ in range(3):
        hands = []
        for _ in range(60):
            hands.append({"stack_deltas": {"player_a": float(rng.normal(2, 5)), "player_b": float(-rng.normal(2, 5))}})
        gen.ingest_session({
            "ended_at": "2026-04-25T00:00:00Z",
            "players": {"player_a": "good/m1", "player_b": "weak/m2"},
            "hands": hands,
            "big_blind": 2.0,
        })

    data = gen.build()
    assert data["methodology_version"] == "v1.1"
    assert len(data["entries"]) == 2
    # 'good/m1' should rank first
    assert data["entries"][0]["model"] == "good/m1"
    assert data["entries"][0]["rank"] == 1
    assert data["entries"][0]["skill_bb_per_100"]["point"] is None
    assert data["entries"][0]["skill_bb_per_100"]["source"] == "not_available"


def test_leaderboard_aggregates_by_model_not_seat():
    gen = LeaderboardGenerator()
    gen.ingest_session({
        "players": {"player_a": "model/one", "player_b": "model/two"},
        "hands": [{"stack_deltas": {"player_a": 1, "player_b": -1}}] * 40,
    })
    gen.ingest_session({
        "players": {"player_a": "model/three", "player_b": "model/two"},
        "hands": [{"stack_deltas": {"player_a": 2, "player_b": -2}}] * 40,
    })
    data = gen.build()
    models = {entry["model"]: entry for entry in data["entries"]}
    assert set(models) == {"model/one", "model/two", "model/three"}
    assert models["model/one"]["hands_played"] == 40
    assert models["model/three"]["hands_played"] == 40
    assert models["model/two"]["hands_played"] == 80


def test_leaderboard_uses_duplicate_templates_for_skill():
    gen = LeaderboardGenerator()
    gen.ingest_session({
        "ended_at": "2026-04-25T00:00:00Z",
        "players": {"player_a": "good/m1", "player_b": "weak/m2"},
        "hands": [{"stack_deltas": {"player_a": 1, "player_b": -1}}] * 40,
        "duplicate_templates": [
            {"rotations": [
                {"player_chips": {"player_a": 30, "player_b": -10}},
                {"player_chips": {"player_a": 25, "player_b": -5}},
            ]},
            {"rotations": [
                {"player_chips": {"player_a": 20, "player_b": -20}},
                {"player_chips": {"player_a": 15, "player_b": -15}},
            ]},
        ],
    })
    data = gen.build()
    models = {entry["model"]: entry for entry in data["entries"]}
    assert models["good/m1"]["skill_bb_per_100"]["source"] == "duplicate_poker"
    assert models["good/m1"]["skill_bb_per_100"]["point"] > 0
    assert models["weak/m2"]["skill_bb_per_100"]["point"] < 0
    assert models["good/m1"]["duplicate_templates"] == 2


def test_leaderboard_includes_harness_metrics():
    gen = LeaderboardGenerator()
    gen.ingest_session({
        "players": {"player_a": "model/one", "player_b": "model/two"},
        "hands": [{"stack_deltas": {"player_a": 1, "player_b": -1}}] * 40,
        "decisions": [
            {
                "player_id": "player_a",
                "model": "model/one",
                "outcome": "valid_action",
                "engine_valid": True,
                "elapsed_sec": 1.0,
                "timeout_fraction": 0.01,
                "write_success": True,
                "permission_error_count": 0,
            },
            {
                "player_id": "player_b",
                "model": "model/two",
                "outcome": "timeout",
                "engine_valid": False,
                "elapsed_sec": 90.0,
                "timeout_fraction": 1.0,
                "write_success": False,
                "permission_error_count": 0,
            },
        ],
    })

    data = gen.build()
    models = {entry["model"]: entry for entry in data["entries"]}
    assert models["model/one"]["harness"]["decisions"] == 1
    assert models["model/one"]["harness"]["valid_action_rate"] == 1.0
    assert models["model/two"]["harness"]["timeouts"] == 1
    assert models["model/one"]["harness"]["harness_score"] > models["model/two"]["harness"]["harness_score"]


def test_leaderboard_eligibility_filter(tmp_path: Path):
    gen = LeaderboardGenerator()
    # Only 100 hands → not eligible (needs 5000)
    gen.ingest_session({
        "ended_at": "2026-04-25T00:00:00Z",
        "players": {"player_a": "m1", "player_b": "m2"},
        "hands": [{"stack_deltas": {"player_a": 1, "player_b": -1}}] * 100,
    })
    eligible = gen.build(only_eligible=True)
    assert eligible["entries"] == []
    all_entries = gen.build(only_eligible=False)
    assert len(all_entries["entries"]) == 2
