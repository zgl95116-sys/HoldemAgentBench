"""End-to-end: heads-up mock-vs-mock through the full orchestrator.

Exercises shim startup is skipped (mock://), but engine + workspace + agent pool
+ recorder + lifecycle all run. No API credits spent.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hab.cli.export import verify_export, write_run_export
from hab.orchestrator.lifecycle import HABSession, SessionConfig


@pytest.mark.asyncio
async def test_e2e_mock_vs_mock_runs_to_completion(tmp_path: Path):
    cfg = SessionConfig(
        players={
            "player_a": "mock://always-fold",
            "player_b": "mock://always-call",
        },
        hands_target=100,
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        output_dir=tmp_path,
        max_concurrent_agents=2,
        decision_timeout_sec=10.0,
        seed=42,
    )
    session = HABSession(cfg)
    result = await asyncio.wait_for(session.run(), timeout=120)

    assert result["hands_played"] >= 1
    final = result["final_stacks"]
    # Total chips should be conserved (no rake).
    assert sum(final.values()) == pytest.approx(400.0, rel=1e-3)

    hand_files = list((session.session_dir / "hands").glob("*.json"))
    assert len(hand_files) == result["hands_played"]
    sample = json.loads(hand_files[0].read_text())
    assert "stack_deltas" in sample
    assert "hand_id" in sample

    # Workspaces should exist with CLAUDE.md
    for pid in cfg.players:
        ws = session.session_dir / "workspaces" / pid
        assert (ws / "CLAUDE.md").exists()

    # Session summary should be written
    summary = json.loads((session.session_dir / "session_summary.json").read_text())
    assert summary["hands_target"] == 100
    assert "final_stacks" in summary
    assert summary["decisions_recorded"] > 0
    assert summary["decision_summary"]["overall"]["valid_actions"] > 0
    assert (session.session_dir / "decision_log.jsonl").exists()


@pytest.mark.asyncio
async def test_e2e_min_raise_vs_call_runs(tmp_path: Path):
    """Sanity check: min-raise-or-call vs always-call doesn't deadlock."""
    cfg = SessionConfig(
        players={
            "player_a": "mock://min-raise-or-call",
            "player_b": "mock://always-call",
        },
        hands_target=20,
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        output_dir=tmp_path,
        max_concurrent_agents=2,
        decision_timeout_sec=10.0,
        seed=7,
    )
    session = HABSession(cfg)
    result = await asyncio.wait_for(session.run(), timeout=60)
    assert result["hands_played"] >= 1
    assert sum(result["final_stacks"].values()) == pytest.approx(400.0, rel=1e-3)


@pytest.mark.asyncio
async def test_e2e_duplicate_session_exports_official_artifact(tmp_path: Path):
    cfg = SessionConfig(
        players={
            "player_a": "mock://always-fold",
            "player_b": "mock://always-fold",
        },
        hands_target=2,
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        output_dir=tmp_path,
        max_concurrent_agents=2,
        decision_timeout_sec=10.0,
        seed=11,
        duplicate_templates=True,
    )
    session = HABSession(cfg)
    await asyncio.wait_for(session.run(), timeout=30)

    export, manifest = write_run_export(session.session_dir, tmp_path / "official" / session.session_id)

    summary = json.loads((session.session_dir / "session_summary.json").read_text())
    assert summary["duplicate_templates_enabled"] is True
    assert len(export["duplicate_templates"]) == 1
    assert export["decisions_recorded"] == summary["decisions_recorded"]
    assert export["decision_summary"]["decisions"] == summary["decisions_recorded"]
    assert {f["path"] for f in manifest["files"]} >= {"run.json", "hands/h_00001.json"}
    assert verify_export(tmp_path / "official" / session.session_id) == []
