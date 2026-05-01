"""End-to-end 6-max with mocks. Verifies workspace creation, skills copy, MCP config."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from hab.orchestrator.lifecycle import HABSession, SessionConfig


@pytest.mark.asyncio
async def test_e2e_6max_mock(tmp_path: Path):
    cfg = SessionConfig(
        players={f"player_{c}": "mock://always-call" for c in "abcdef"},
        hands_target=10,
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        output_dir=tmp_path,
        max_concurrent_agents=6,
        decision_timeout_sec=10.0,
        seed=42,
    )
    session = HABSession(cfg)
    result = await asyncio.wait_for(session.run(), timeout=60)

    assert result["hands_played"] >= 1
    assert sum(result["final_stacks"].values()) == pytest.approx(1200.0)

    # Each workspace should have skills copied + .claude/mcp_servers.json
    for pid in cfg.players:
        ws = session.session_dir / "workspaces" / pid
        assert (ws / "CLAUDE.md").exists()
        assert (ws / "skills" / "meta-strategy" / "SKILL.md").exists()
        assert (ws / "skills" / "poker-fundamentals" / "SKILL.md").exists()
        mcp_cfg = ws / ".claude" / "mcp_servers.json"
        assert mcp_cfg.exists()
        cfg_json = json.loads(mcp_cfg.read_text())
        assert "hab-poker-toolkit" in cfg_json["mcpServers"]
