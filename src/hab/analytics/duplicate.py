"""Layer 2: Duplicate Poker analyzer.

Each `template` is a fixed card sequence played multiple times with different
position rotations. Skill BB/100 = mean delta-from-template-average.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import numpy as np


@dataclass
class DuplicateResult:
    player_id: str
    skill_bb_per_100: float
    ci_low: float
    ci_high: float
    n_templates: int


class DuplicatePokerAnalyzer:
    def __init__(self, big_blind: float = 2.0):
        self.big_blind = big_blind

    def analyze(self, templates: list[dict]) -> dict[str, DuplicateResult]:
        """templates is a list of dicts:
          {"rotations": [{"player_chips": {pid: chips_won, ...}}, ...]}
        For each template, each rotation contributes one chips-won number per player.
        Each player's skill delta for this template = avg chips - template avg.
        """
        player_deltas: dict[str, list[float]] = defaultdict(list)

        for template in templates:
            all_chips: list[float] = []
            for rotation in template["rotations"]:
                all_chips.extend(rotation["player_chips"].values())
            template_avg = float(np.mean(all_chips)) if all_chips else 0.0

            per_player: dict[str, list[float]] = defaultdict(list)
            for rotation in template["rotations"]:
                for pid, chips in rotation["player_chips"].items():
                    per_player[pid].append(float(chips))

            for pid, chips_list in per_player.items():
                avg = float(np.mean(chips_list))
                player_deltas[pid].append(avg - template_avg)

        results: dict[str, DuplicateResult] = {}
        for pid, deltas in player_deltas.items():
            point, (ci_low, ci_high) = self._bootstrap_skill(deltas)
            results[pid] = DuplicateResult(
                player_id=pid,
                skill_bb_per_100=point,
                ci_low=ci_low,
                ci_high=ci_high,
                n_templates=len(deltas),
            )
        return results

    def _bootstrap_skill(
        self, deltas: list[float], n_bootstrap: int = 10000, seed: int = 42
    ) -> tuple[float, tuple[float, float]]:
        if len(deltas) < 5:
            point = float(np.mean(deltas)) / self.big_blind * 100 if deltas else 0.0
            return point, (float("-inf"), float("inf"))
        arr = np.array(deltas, dtype=float)
        rng = np.random.default_rng(seed)
        means = np.empty(n_bootstrap)
        n = len(arr)
        for i in range(n_bootstrap):
            sample = rng.choice(arr, size=n, replace=True)
            means[i] = sample.mean() / self.big_blind * 100
        point = float(arr.mean()) / self.big_blind * 100
        return point, (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))
