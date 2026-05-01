"""Aggregates session run JSONs into a leaderboard JSON for docs/data/."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from hab.analytics.elo import EloSystem
from hab.analytics.stats import PlayerStats, bootstrap_ci
from hab.orchestrator.decision_metrics import summarize_decisions

# Eligibility (from spec §13.5)
MIN_HANDS = 5000
MIN_SESSIONS = 3


class LeaderboardGenerator:
    def __init__(
        self,
        big_blind: float = 2.0,
        methodology_version: str = "v1.1",
        preset: str = "daily-bench",
        format_: str = "6-max",
        elo_initial: float = 1500.0,
        elo_k: float = 32.0,
    ):
        self.big_blind = big_blind
        self.methodology_version = methodology_version
        self.preset = preset
        self.format = format_
        self.elo = EloSystem(initial_rating=elo_initial, k_factor=elo_k)
        self._aggregate: dict[str, dict] = {}

    @staticmethod
    def _model_for_player(session_json: dict, player_id: str) -> str:
        return session_json.get("players", {}).get(player_id, player_id)

    @staticmethod
    def _empty_aggregate(model: str) -> dict:
        return {
            "model": model,
            "all_deltas": [],
            "skill_deltas": [],
            "decision_records": [],
            "sessions": 0,
            "hands": 0,
            "duplicate_templates": 0,
            "tool_calls_used": 0,
            "last_run": None,
        }

    def _duplicate_skill_deltas(self, session_json: dict) -> dict[str, list[float]]:
        """Return per-model duplicate-poker skill deltas in chips.

        Supported schema:
          {"duplicate_templates": [
             {"rotations": [{"player_chips": {"player_a": 10, ...}}, ...]}
          ]}
        """
        templates = (
            session_json.get("duplicate_templates")
            or session_json.get("templates")
            or []
        )
        out: dict[str, list[float]] = {}
        for template in templates:
            rotations = template.get("rotations") or []
            all_chips: list[float] = []
            per_model: dict[str, list[float]] = {}
            for rotation in rotations:
                chips_by_player = rotation.get("player_chips") or {}
                for pid, chips in chips_by_player.items():
                    model = self._model_for_player(session_json, pid)
                    chips_f = float(chips)
                    all_chips.append(chips_f)
                    per_model.setdefault(model, []).append(chips_f)
            if not all_chips:
                continue
            template_avg = sum(all_chips) / len(all_chips)
            for model, chips in per_model.items():
                out.setdefault(model, []).append(sum(chips) / len(chips) - template_avg)
        return out

    def ingest_session(self, session_json: dict) -> None:
        """Update Elo + accumulator from a single session result.

        session_json should look like:
          {
            "ended_at": <iso>,
            "players": {player_id: model_id, ...},
            "hands": [{"stack_deltas": {...}, ...}, ...],   # per-hand
            "preset": "daily-bench",
            ...
          }
        """
        hands = session_json.get("hands") or session_json.get("history") or []
        big_blind = session_json.get("big_blind", self.big_blind)
        per_model_deltas: dict[str, list[float]] = {}
        for h in hands:
            for pid, d in (h.get("stack_deltas") or {}).items():
                model = self._model_for_player(session_json, pid)
                per_model_deltas.setdefault(model, []).append(float(d))

        if not per_model_deltas:
            return

        # Build Elo input: bb/100 + CI per player
        session_elo_input: dict[str, dict] = {}
        skill_deltas = self._duplicate_skill_deltas(session_json)
        ended_at = session_json.get("ended_at")
        for model, deltas in per_model_deltas.items():
            point, (ci_low, ci_high) = bootstrap_ci(deltas, big_blind, n_bootstrap=2000)
            session_elo_input[model] = {"bb_per_100": point, "ci": (ci_low, ci_high)}

            agg = self._aggregate.setdefault(model, self._empty_aggregate(model))
            agg["all_deltas"].extend(deltas)
            agg["skill_deltas"].extend(skill_deltas.get(model, []))
            agg["sessions"] += 1
            agg["hands"] += len(deltas)
            agg["duplicate_templates"] += len(skill_deltas.get(model, []))
            if ended_at:
                agg["last_run"] = max(agg["last_run"] or ended_at, ended_at)

        for record in session_json.get("decisions") or []:
            player_id = record.get("player_id")
            model = record.get("model") or (
                self._model_for_player(session_json, player_id) if player_id else "unknown"
            )
            agg = self._aggregate.setdefault(model, self._empty_aggregate(model))
            normalized = dict(record)
            normalized["model"] = model
            agg["decision_records"].append(normalized)

        self.elo.update_after_session(session_elo_input)

    def build(self, only_eligible: bool = False) -> dict:
        entries: list[dict] = []
        for entry in self.elo.leaderboard():
            model = entry.player_id
            # Find aggregated stats by model (might be keyed by player_id; we kept model in agg)
            agg = next(
                (v for v in self._aggregate.values() if v["model"] == model),
                None,
            )
            if agg is None:
                continue
            if only_eligible and (
                agg["hands"] < MIN_HANDS
                or agg["sessions"] < MIN_SESSIONS
                or agg["duplicate_templates"] == 0
            ):
                continue
            skill_point = None
            skill_low = None
            skill_high = None
            skill_source = "not_available"
            if agg["skill_deltas"]:
                skill_point, (skill_low, skill_high) = bootstrap_ci(
                    agg["skill_deltas"], self.big_blind, n_bootstrap=2000
                )
                skill_source = "duplicate_poker"
            ps = PlayerStats(
                player_id=model, hand_results=agg["all_deltas"], big_blind=self.big_blind
            )
            harness = summarize_decisions(agg["decision_records"])["overall"]
            entries.append({
                "rank": 0,  # filled in below
                "model": model,
                "display_name": model.split("/")[-1] if "/" in model else model,
                "elo": round(entry.rating, 0),
                "skill_bb_per_100": {
                    "point": round(skill_point, 2) if skill_point is not None else None,
                    "ci_low": round(skill_low, 2) if skill_low is not None and skill_low > float("-inf") else None,
                    "ci_high": round(skill_high, 2) if skill_high is not None and skill_high < float("inf") else None,
                    "source": skill_source,
                },
                "raw_bb_per_100": round(ps.bb_per_100, 2),
                "hands_played": agg["hands"],
                "sessions_played": agg["sessions"],
                "duplicate_templates": agg["duplicate_templates"],
                "harness": harness,
                "tier": "official",
                "last_run": (
                    str(agg["last_run"])[:10]
                    if agg["last_run"]
                    else datetime.now(timezone.utc).strftime("%Y-%m-%d")
                ),
            })
        for i, e in enumerate(entries, 1):
            e["rank"] = i
        return {
            "methodology_version": self.methodology_version,
            "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "preset": self.preset,
            "format": self.format,
            "entries": entries,
        }

    def write(self, output_path: Path, only_eligible: bool = False) -> dict:
        data = self.build(only_eligible=only_eligible)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(data, indent=2))
        return data
