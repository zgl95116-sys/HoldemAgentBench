"""Layer 3: Elo rating with CI-overlap-aware comparisons."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class EloEntry:
    player_id: str
    rating: float
    games_played: int


class EloSystem:
    def __init__(self, initial_rating: float = 1500.0, k_factor: float = 32.0):
        self.initial_rating = initial_rating
        self.k = k_factor
        self.ratings: dict[str, float] = {}
        self.games: dict[str, int] = {}

    def get(self, player_id: str) -> float:
        return self.ratings.get(player_id, self.initial_rating)

    def update_after_session(self, session_results: dict[str, dict]) -> None:
        """session_results[player_id] = {"bb_per_100": float, "ci": (low, high)}."""
        players = list(session_results.keys())
        new_ratings = {p: self.get(p) for p in players}

        for i, a in enumerate(players):
            for b in players[i + 1:]:
                score_a = self._compare(session_results[a], session_results[b])
                ra = self.get(a)
                rb = self.get(b)
                expected_a = 1 / (1 + 10 ** ((rb - ra) / 400))
                delta = self.k * (score_a - expected_a)
                new_ratings[a] += delta
                new_ratings[b] -= delta

        for p, r in new_ratings.items():
            self.ratings[p] = r
            self.games[p] = self.games.get(p, 0) + 1

    @staticmethod
    def _compare(a: dict, b: dict) -> float:
        a_low, a_high = a["ci"]
        b_low, b_high = b["ci"]

        # CI overlap (when both finite) -> draw
        if a_low > float("-inf") and b_low > float("-inf"):
            if a_low <= b_high and b_low <= a_high:
                return 0.5

        if a["bb_per_100"] > b["bb_per_100"]:
            return 1.0
        if a["bb_per_100"] < b["bb_per_100"]:
            return 0.0
        return 0.5

    def leaderboard(self) -> list[EloEntry]:
        return sorted(
            [
                EloEntry(p, r, self.games.get(p, 0))
                for p, r in self.ratings.items()
            ],
            key=lambda x: -x.rating,
        )
