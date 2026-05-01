import json
import os
from pathlib import Path

import pytest

from hab.orchestrator.agent_pool import (
    _ALLOWED_REAL_AGENT_TOOLS,
    AgentPool,
    _parse_action_lenient,
)
from hab.engine.recorder import HandRecorder


def test_lenient_parser_type_alias():
    a = _parse_action_lenient('{"type":"call","amount":2.0}', "h_1")
    assert a.action == "call"
    assert a.amount == 2.0
    assert a.hand_id == "h_1"


def test_lenient_parser_with_code_fences():
    raw = '```json\n{"action":"raise","amount":4}\n```'
    a = _parse_action_lenient(raw, "h_1")
    assert a.action == "raise"


def test_lenient_parser_with_prose_wrap():
    raw = 'Sure, here is my decision:\n{"action":"fold"}\nLet me know.'
    a = _parse_action_lenient(raw, "h_2")
    assert a.action == "fold"


def test_lenient_parser_normalizes_bet_to_raise():
    a = _parse_action_lenient('{"action":"bet","amount":10}', "h_1")
    assert a.action == "raise"
    assert a.amount == 10


def test_lenient_parser_action_type_alias():
    """gpt-4o-mini observed in the wild writing 'action_type' instead of 'action'."""
    a = _parse_action_lenient('{"action_type":"raise","amount":5,"hand_id":"h_00002"}', None)
    assert a.action == "raise"
    assert a.amount == 5


def test_lenient_parser_choice_alias():
    a = _parse_action_lenient('{"choice":"fold"}', "h_1")
    assert a.action == "fold"


@pytest.mark.asyncio
async def test_mock_always_fold(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "game_view").mkdir(parents=True)
    (ws / "actions").mkdir(parents=True)
    (ws / "game_view" / "current_state.json").write_text(json.dumps({
        "hand_id": "h_1",
        "legal_actions": [{"type": "fold"}, {"type": "call", "amount": 2.0}],
    }))
    pool = AgentPool(shim_url="http://x", max_concurrent=1)
    a = await pool.request_action("p", "mock://always-fold", ws, "h_1")
    assert a.action == "fold"
    record = pool.pop_decision_record("p", "h_1")
    assert record is not None
    assert record["model"] == "mock://always-fold"
    assert record["outcome"] == "valid_action"
    assert record["write_success"] is True


@pytest.mark.asyncio
async def test_mock_always_call(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "game_view").mkdir(parents=True)
    (ws / "actions").mkdir(parents=True)
    (ws / "game_view" / "current_state.json").write_text(json.dumps({
        "hand_id": "h_1",
        "legal_actions": [{"type": "fold"}, {"type": "call", "amount": 2.0}],
    }))
    pool = AgentPool(shim_url="http://x", max_concurrent=1)
    a = await pool.request_action("p", "mock://always-call", ws, "h_1")
    assert a.action == "call"
    assert a.amount == 2.0


@pytest.mark.asyncio
async def test_mock_min_raise(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "game_view").mkdir(parents=True)
    (ws / "actions").mkdir(parents=True)
    (ws / "game_view" / "current_state.json").write_text(json.dumps({
        "hand_id": "h_1",
        "legal_actions": [
            {"type": "fold"},
            {"type": "call", "amount": 2.0},
            {"type": "raise", "amount_min": 4.0, "amount_max": 198.0},
        ],
    }))
    pool = AgentPool(shim_url="http://x", max_concurrent=1)
    a = await pool.request_action("p", "mock://min-raise-or-call", ws, "h_1")
    assert a.action == "raise"
    assert a.amount == 4.0


def test_time_bank_initialized_per_player():
    pool = AgentPool(
        shim_url="http://x",
        max_concurrent=1,
        players=["a", "b"],
        decision_clock_sec=30,
        time_bank_tokens=5,
        time_bank_token_sec=60,
    )
    assert pool.bank_remaining["a"] == 300
    assert pool.bank_remaining["b"] == 300


def test_session_ids_stable_per_player():
    pool = AgentPool(
        shim_url="http://x",
        max_concurrent=1,
        players=["a", "b"],
    )
    assert pool.session_ids["a"] != pool.session_ids["b"]
    # UUIDs are 36-char strings
    assert len(pool.session_ids["a"]) == 36
    # session_started starts empty (no calls yet)
    assert pool.session_started == {}


def test_consume_bank_only_charges_overage():
    pool = AgentPool(
        shim_url="http://x",
        max_concurrent=1,
        players=["a"],
        decision_clock_sec=30,
        time_bank_tokens=5,
        time_bank_token_sec=60,
    )
    # Decision under base clock: bank untouched
    pool._consume_bank("a", 25)
    assert pool.bank_remaining["a"] == 300
    # Decision over base by 70s: bank deducted 70
    pool._consume_bank("a", 100)
    assert pool.bank_remaining["a"] == 230
    # Going over by way more than bank: clamped to 0
    pool._consume_bank("a", 10000)
    assert pool.bank_remaining["a"] == 0


def test_build_env_uses_allowlist_not_host_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "real-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "also-secret")
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    pool = AgentPool(
        shim_url="http://shim",
        max_concurrent=1,
        player_tokens={"p": "hab-token"},
    )
    env = pool._build_env("p", "openai/gpt-5", tmp_path)
    assert env["ANTHROPIC_API_KEY"] == "hab-token"
    assert env["ANTHROPIC_AUTH_TOKEN"] == "hab-token"
    assert env["ANTHROPIC_BASE_URL"] == "http://shim"
    assert env["ANTHROPIC_MODEL"] == "openai/gpt-5"
    assert env["PLAYER_ID"] == "p"
    assert env["HOME"] == str(tmp_path / ".agent_home")
    assert "PATH" in env
    assert "OPENROUTER_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env


def test_build_env_bootstraps_isolated_claude_home(tmp_path: Path):
    pool = AgentPool(
        shim_url="http://shim",
        max_concurrent=1,
        player_tokens={"p": "hab-token"},
    )

    env = pool._build_env("p", "openai/gpt-5", tmp_path)
    agent_home = Path(env["HOME"])
    settings = json.loads((agent_home / ".claude" / "settings.json").read_text())
    state = json.loads((agent_home / ".claude.json").read_text())

    assert settings["theme"] == "dark"
    assert settings["skipAutoPermissionPrompt"] is True
    assert state["hasCompletedOnboarding"] is True
    assert state["customApiKeyResponses"]["approved"] == ["hab-token"]
    project_paths = set(state["projects"])
    assert str(tmp_path) in project_paths
    assert str(tmp_path.resolve()) in project_paths
    assert state["projects"][str(tmp_path)]["hasTrustDialogAccepted"] is True
    assert not (agent_home / ".claude" / "projects").exists()
    assert not (agent_home / ".claude" / "history.jsonl").exists()


def test_unsafe_permissions_are_opt_in():
    pool = AgentPool(shim_url="http://x")
    assert pool.unsafe_skip_permissions is False
    unsafe = AgentPool(shim_url="http://x", unsafe_skip_permissions=True)
    assert unsafe.unsafe_skip_permissions is True


def test_safe_real_agent_tool_allowlist_is_narrow():
    assert "Write" in _ALLOWED_REAL_AGENT_TOOLS
    assert "Edit" in _ALLOWED_REAL_AGENT_TOOLS
    assert "mcp__hab-poker-toolkit__gto_lookup" in _ALLOWED_REAL_AGENT_TOOLS
    assert "Bash" not in _ALLOWED_REAL_AGENT_TOOLS
    assert "Bash(python3 *)" not in _ALLOWED_REAL_AGENT_TOOLS


def test_persistent_claude_cmd_sets_fast_effort_and_blocks_bash(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("bench prompt")
    pool = AgentPool(
        shim_url="http://x",
        claude_effort="low",
    )
    cmd = pool._persistent_claude_cmd(
        workspace=tmp_path,
        player_id="p",
        session_id="00000000-0000-4000-8000-000000000001",
    )
    assert cmd[cmd.index("--effort") + 1] == "low"
    assert cmd[cmd.index("--disallowedTools") + 1] == "Bash"


def test_recorder_primes_action_file_for_edit_tool(tmp_path: Path):
    ws = tmp_path / "ws"
    recorder = HandRecorder(tmp_path / "session")
    recorder.reset_action_dir(ws)
    assert (ws / "actions" / "action.json").read_text() == "{}\n"


@pytest.mark.asyncio
async def test_real_action_missing_binary(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "game_view").mkdir(parents=True)
    (ws / "actions").mkdir(parents=True)
    (ws / "game_view" / "current_state.json").write_text("{}")
    pool = AgentPool(
        shim_url="http://x",
        max_concurrent=1,
        claude_binary="/definitely/not/here/claude",
    )
    a = await pool.request_action("p", "openai/gpt-5", ws, "h_1")
    assert a.action == "fold"
    assert "spawn_failed" in (a.reason or "")
    record = pool.pop_decision_record("p", "h_1")
    assert record is not None
    assert record["outcome"] == "spawn_failed"
    assert record["write_success"] is False
    assert (ws / "actions" / "action.json").read_text() == "{}\n"


@pytest.mark.asyncio
async def test_openrouter_runtime_uses_persistent_agent(tmp_path: Path):
    ws = tmp_path / "session" / "workspaces" / "p"
    (ws / "game_view").mkdir(parents=True)
    (ws / "actions").mkdir(parents=True)
    (ws / "game_view" / "current_state.json").write_text(json.dumps({
        "hand_id": "h_1",
        "legal_actions": [{"type": "fold"}, {"type": "call", "amount": 2.0}],
    }))
    (ws / "game_view" / "hole_cards.json").write_text(json.dumps({
        "hand_id": "h_1",
        "cards": ["As", "Ah"],
    }))

    class FakeAgent:
        calls = 0

        async def decide(self, *, workspace, hand_id, effective_timeout_sec):
            FakeAgent.calls += 1
            return (
                _parse_action_lenient('{"action":"call","amount":2}', hand_id),
                {
                    "final_content": '{"action":"call","amount":2}',
                    "tool_calls_used": ["gto_lookup"],
                    "mcp_tool_call_count": 1,
                },
            )

        async def aclose(self):
            pass

    pool = AgentPool(
        shim_url="http://x",
        max_concurrent=1,
        players=["p"],
        agent_runtime="openrouter",
        openrouter_key="test-key",
    )
    pool._openrouter_agents["p"] = FakeAgent()

    action = await pool.request_action("p", "z-ai/glm-5.1", ws, "h_1")
    record = pool.pop_decision_record("p", "h_1")

    assert action.action == "call"
    assert FakeAgent.calls == 1
    assert record is not None
    assert record["agent_kind"] == "openrouter"
    assert record["api_runtime"] == "openrouter"
    assert record["mcp_tool_call_count"] == 1
    assert record["tool_calls_used"] == ["gto_lookup"]


@pytest.mark.asyncio
async def test_claude_code_persistent_runtime_reuses_injected_agent(tmp_path: Path):
    ws = tmp_path / "session" / "workspaces" / "p"
    (ws / "game_view").mkdir(parents=True)
    (ws / "actions").mkdir(parents=True)
    (ws / "game_view" / "current_state.json").write_text(json.dumps({
        "hand_id": "h_1",
        "legal_actions": [{"type": "fold"}],
    }))
    (ws / "game_view" / "hole_cards.json").write_text(json.dumps({
        "hand_id": "h_1",
        "cards": ["As", "Ah"],
    }))

    class FakePersistentClaude:
        calls = 0

        async def request_action(self, *, prompt, action_path, hand_id, timeout):
            FakePersistentClaude.calls += 1
            action_path.write_text('{"action":"fold"}')
            return (
                _parse_action_lenient('{"action":"fold"}', hand_id),
                {
                    "raw": '{"action":"fold"}',
                    "process_id": 123,
                },
            )

        async def close(self, *, kill=False):
            pass

    pool = AgentPool(
        shim_url="http://x",
        max_concurrent=1,
        players=["p"],
        agent_runtime="claude-code-persistent",
    )
    pool._persistent_claude_agents["p"] = FakePersistentClaude()

    action = await pool.request_action("p", "openai/gpt-5", ws, "h_1")
    record = pool.pop_decision_record("p", "h_1")

    assert action.action == "fold"
    assert FakePersistentClaude.calls == 1
    assert record is not None
    assert record["agent_kind"] == "claude-code-persistent"
    assert record["api_runtime"] == "claude-code-persistent"
    assert record["persistent_process_id"] == 123
