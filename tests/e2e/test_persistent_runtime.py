"""End-to-end test for the claude-code-persistent runtime.

Gated on HAB_E2E_CLAUDE=1 because it requires:
  - the `claude` CLI on PATH
  - OPENROUTER_API_KEY exported
  - ~5-10 minutes wall time
  - real OpenRouter spend (~$0.10)

    HAB_E2E_CLAUDE=1 OPENROUTER_API_KEY=sk-or-... \\
        pytest tests/e2e/test_persistent_runtime.py -v -s
"""
from __future__ import annotations
import json
import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HAB_E2E_CLAUDE") != "1"
    or not os.environ.get("OPENROUTER_API_KEY"),
    reason="HAB_E2E_CLAUDE=1 + OPENROUTER_API_KEY required",
)


@pytest.mark.asyncio
async def test_persistent_runtime_completes_one_real_decision(tmp_path: Path):
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found on PATH")

    from hab.orchestrator.lifecycle import HABSession, SessionConfig

    cfg = SessionConfig(
        players={
            "player_a": "deepseek/deepseek-v3.2",
            "player_b": "mock://always-fold",
        },
        hands_target=1,
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        output_dir=tmp_path,
        seed=42,
        decision_clock_sec=300.0,
        time_bank_tokens=3,
        time_bank_token_sec=60.0,
        decision_timeout_sec=600.0,
        agent_runtime="claude-code-persistent",
        openrouter_key=os.environ["OPENROUTER_API_KEY"],
        live=False,
    )
    session = HABSession(cfg)
    result = await session.run()

    assert result["hands_played"] == 1, f"expected 1 hand, got {result['hands_played']}"

    decisions = [
        json.loads(line)
        for line in (
            session.session_dir / "decision_log.jsonl"
        ).read_text().splitlines()
    ]
    real = [d for d in decisions if d["model"] == "deepseek/deepseek-v3.2"]
    assert real, "the real model played zero decisions"

    # The whole point of the runtime: the agent must produce real
    # decisions, not just timeout-folds. At least one valid action.
    valid = [d for d in real if d["outcome"] == "valid_action"]
    assert valid, (
        f"all of the real model's decisions timed out or errored: "
        f"{[d['outcome'] for d in real]}"
    )
    # And at least one MCP poker tool must have been reachable.
    total_tools = sum(d.get("mcp_tool_call_count", 0) for d in real)
    assert total_tools >= 1, (
        f"agent never called an MCP tool — toolkit not exposed correctly. "
        f"Decisions: {[(d['outcome'], d.get('mcp_tool_call_count', 0)) for d in real]}"
    )
