import json
from pathlib import Path

import pytest

from hab.orchestrator.openrouter_agent import OpenRouterPersistentAgent


class _FakeResponse:
    def __init__(self, data: dict):
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.requests: list[dict] = []

    async def post(self, _url, *, headers, json):
        self.requests.append({"headers": headers, "json": json})
        return _FakeResponse(self.responses.pop(0))


def _write_spot(workspace: Path):
    (workspace / "game_view").mkdir(parents=True)
    (workspace / "actions").mkdir(parents=True)
    (workspace / "game_view" / "current_state.json").write_text(json.dumps({
        "hand_id": "h_1",
        "table_id": "table_1",
        "street": "preflop",
        "board": [],
        "pot": 3,
        "to_act": "player_a",
        "stacks": {"player_a": 199, "player_b": 198},
        "current_bet": 1,
        "action_history": [],
        "legal_actions": [
            {"type": "fold"},
            {"type": "call", "amount": 1},
            {"type": "raise", "amount_min": 4, "amount_max": 200},
        ],
        "big_blind": 2,
        "small_blind": 1,
    }))
    (workspace / "game_view" / "hole_cards.json").write_text(json.dumps({
        "hand_id": "h_1",
        "cards": ["As", "Ah"],
    }))


@pytest.mark.asyncio
async def test_openrouter_agent_executes_tools_and_persists_context(tmp_path: Path):
    workspace = tmp_path / "session" / "workspaces" / "player_a"
    _write_spot(workspace)
    client = _FakeClient([
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "gto_lookup",
                            "arguments": json.dumps({
                                "position_scenario": "HU_SB_open",
                                "my_cards": ["As", "Ah"],
                            }),
                        },
                    }],
                },
            }]
        },
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "content": json.dumps({
                        "action": "raise",
                        "amount": 4,
                        "reason": "AA opens for value",
                    })
                },
            }]
        },
    ])
    agent = OpenRouterPersistentAgent(
        player_id="player_a",
        model="z-ai/glm-5.1",
        api_key="test-key",
        http_client=client,
    )

    action, meta = await agent.decide(
        workspace=workspace,
        hand_id="h_1",
        effective_timeout_sec=30,
    )

    assert action.action == "raise"
    assert action.amount == 4
    assert action.tool_calls_used == ["gto_lookup"]
    assert meta["mcp_tool_call_count"] == 1
    assert len(client.requests) == 2
    assert any(m.get("role") == "tool" for m in client.requests[1]["json"]["messages"])
    assert (workspace / "actions" / "action.json").exists()
