"""Persists game state and actions to JSON files for the agents to read/write."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hab.engine.actions import Action
from hab.engine.state import GameView, HandResult, HoleCards


class HandRecorder:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.hands_dir = session_dir / "hands"
        self.decision_log_path = session_dir / "decision_log.jsonl"
        self.hands_dir.mkdir(parents=True, exist_ok=True)

    def write_game_view(self, workspace: Path, view: GameView, hole: HoleCards) -> None:
        gv_dir = workspace / "game_view"
        gv_dir.mkdir(parents=True, exist_ok=True)
        (gv_dir / "current_state.json").write_text(view.model_dump_json(indent=2))
        (gv_dir / "hole_cards.json").write_text(hole.model_dump_json(indent=2))

    def reset_action_dir(self, workspace: Path) -> None:
        adir = workspace / "actions"
        adir.mkdir(parents=True, exist_ok=True)
        af = adir / "action.json"
        af.write_text("{}\n")

    def read_action(self, workspace: Path) -> Action | None:
        af = workspace / "actions" / "action.json"
        if not af.exists():
            return None
        try:
            return Action.model_validate_json(af.read_text())
        except Exception:
            return None

    def write_hand_result(self, result: HandResult) -> None:
        path = self.hands_dir / f"{result.hand_id}.json"
        path.write_text(result.model_dump_json(indent=2))

    def write_decision_record(self, record: dict[str, Any]) -> None:
        with self.decision_log_path.open("a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
