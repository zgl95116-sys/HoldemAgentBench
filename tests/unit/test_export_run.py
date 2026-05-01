import json
from pathlib import Path

from hab.cli.export import build_run_export, verify_export, write_run_export
from scripts.update_leaderboard import validate_run_policy


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")


def test_export_sanitizes_hidden_cards_and_builds_duplicate_templates(tmp_path: Path):
    session_dir = tmp_path / "session"
    _write_json(
        session_dir / "session_summary.json",
        {
            "session_id": "s1",
            "ended_at": "2026-04-28T00:00:00Z",
            "players": {"player_a": "model/a", "player_b": "model/b"},
            "hands_target": 2,
            "hands_played": 2,
            "small_blind": 1,
            "big_blind": 2,
            "starting_stack": 200,
            "final_stacks": {"player_a": 201, "player_b": 199},
            "duplicate_templates_enabled": True,
            "duplicate_mode": "template_rotation",
            "agent_runtime": "openrouter",
        },
    )
    base_hand = {
        "winner": "player_a",
        "pot": 3,
        "showdown_cards": {},
        "hole_cards": {"player_a": ["As", "Ah"], "player_b": ["Kd", "Kh"]},
        "board": [],
        "action_history": [],
        "starting_stacks": {"player_a": 200, "player_b": 200},
        "button": "player_a",
        "duplicate_template_id": "t_00001",
    }
    _write_json(
        session_dir / "hands" / "h_00001.json",
        {
            **base_hand,
            "hand_id": "h_00001",
            "stack_deltas": {"player_a": 1, "player_b": -1},
            "duplicate_rotation": 0,
        },
    )
    _write_json(
        session_dir / "hands" / "h_00002.json",
        {
            **base_hand,
            "hand_id": "h_00002",
            "stack_deltas": {"player_a": -1, "player_b": 1},
            "duplicate_rotation": 1,
        },
    )
    _write_jsonl(
        session_dir / "decision_log.jsonl",
        [
            {
                "schema_version": "hab.decision.v1",
                "hand_id": "h_00001",
                "player_id": "player_a",
                "model": "model/a",
                "outcome": "valid_action",
                "engine_valid": True,
                "elapsed_sec": 1.0,
                "timeout_fraction": 0.01,
                "write_success": True,
                "tool_calls_used": [],
                "permission_error_count": 0,
                "raw_action_bytes": 0,
            }
        ],
    )

    export = build_run_export(session_dir)

    assert export["hands_recorded"] == 2
    assert export["decisions_recorded"] == 1
    assert export["decision_summary"]["per_model"]["model/a"]["valid_actions"] == 1
    assert export["decisions"][0]["raw_action_bytes"] == 0
    assert export["agent_runtime"] == "openrouter"
    assert export["chip_accounting"] == "duplicate_rebuy_net"
    assert export["agent_security"]["unsafe_permissions"] is False
    assert export["hands"][0]["hole_cards"] == {}
    assert export["duplicate_templates"] == [
        {
            "template_id": "t_00001",
            "rotations": [
                {
                    "hand_id": "h_00001",
                    "rotation": 0,
                    "button": "player_a",
                    "player_chips": {"player_a": 1, "player_b": -1},
                },
                {
                    "hand_id": "h_00002",
                    "rotation": 1,
                    "button": "player_a",
                    "player_chips": {"player_a": -1, "player_b": 1},
                },
            ],
        }
    ]


def test_write_run_export_emits_verifiable_manifest(tmp_path: Path):
    session_dir = tmp_path / "session"
    _write_json(
        session_dir / "session_summary.json",
        {
            "session_id": "s1",
            "players": {"player_a": "model/a", "player_b": "model/b"},
            "hands_played": 1,
        },
    )
    _write_json(
        session_dir / "hands" / "h_00001.json",
        {
            "hand_id": "h_00001",
            "winner": "player_a",
            "pot": 3,
            "stack_deltas": {"player_a": 1, "player_b": -1},
            "showdown_cards": {},
            "hole_cards": {"player_a": ["As", "Ah"]},
        },
    )
    output_dir = tmp_path / "official" / "s1"

    export, manifest = write_run_export(session_dir, output_dir)

    assert export["schema_version"] == "hab.run.v1"
    assert {f["path"] for f in manifest["files"]} == {
        "run.json",
        "hands/h_00001.json",
    }
    assert verify_export(output_dir) == []
    assert "As" not in (output_dir / "run.json").read_text()


def test_leaderboard_policy_rejects_unsafe_public_runs(tmp_path: Path):
    errors = validate_run_policy(
        {
            "schema_version": "hab.run.v1",
            "agent_security": {"unsafe_permissions": True},
            "privacy": {"contains_private_workspaces": False},
        },
        tmp_path / "run.json",
    )
    assert errors == [
        "unsafe agent permissions are not allowed on public leaderboards"
    ]
