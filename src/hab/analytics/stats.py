"""Layer 1: Raw BB/100 with bootstrap confidence interval."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def calculate_bb_per_100(hand_results: list[float], big_blind: float) -> float:
    """hand_results: list of per-hand chip deltas. Returns BB/100."""
    if not hand_results:
        return 0.0
    in_bb = sum(hand_results) / big_blind
    return in_bb * 100 / len(hand_results)


def bootstrap_ci(
    hand_results: list[float],
    big_blind: float,
    n_bootstrap: int = 10000,
    confidence: float = 0.95,
    seed: int = 42,
) -> tuple[float, tuple[float, float]]:
    if len(hand_results) < 30:
        point = calculate_bb_per_100(hand_results, big_blind)
        return point, (float("-inf"), float("inf"))

    arr = np.array(hand_results, dtype=float)
    n = len(arr)
    rng = np.random.default_rng(seed)
    means = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        sample = rng.choice(arr, size=n, replace=True)
        means[i] = sample.mean() / big_blind * 100

    point = float(arr.mean()) / big_blind * 100
    alpha = (1 - confidence) / 2
    ci_low = float(np.percentile(means, alpha * 100))
    ci_high = float(np.percentile(means, (1 - alpha) * 100))
    return point, (ci_low, ci_high)


@dataclass
class PlayerStats:
    player_id: str
    hand_results: list[float]
    big_blind: float = 2.0

    @property
    def hands_played(self) -> int:
        return len(self.hand_results)

    @property
    def total_chips(self) -> float:
        return sum(self.hand_results)

    @property
    def bb_per_100(self) -> float:
        return calculate_bb_per_100(self.hand_results, self.big_blind)

    def confidence_interval(self, confidence: float = 0.95, seed: int = 42) -> tuple[float, tuple[float, float]]:
        return bootstrap_ci(self.hand_results, self.big_blind, confidence=confidence, seed=seed)


def aggregate_from_hands(hands: list[dict], big_blind: float = 2.0) -> dict[str, PlayerStats]:
    """Build per-player PlayerStats from hand-result JSONs (dict with 'stack_deltas')."""
    deltas: dict[str, list[float]] = {}
    for h in hands:
        for pid, d in (h.get("stack_deltas") or {}).items():
            deltas.setdefault(pid, []).append(float(d))
    return {
        pid: PlayerStats(player_id=pid, hand_results=ds, big_blind=big_blind)
        for pid, ds in deltas.items()
    }
