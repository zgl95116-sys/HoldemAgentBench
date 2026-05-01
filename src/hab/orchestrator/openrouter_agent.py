"""Persistent OpenRouter-backed agent runtime.

This runtime keeps one chat history per player and executes HAB poker tools
directly in-process. It is much faster than spawning `claude -p` for every
decision, while preserving the core benchmark loop: read current state, use
tools, produce an action JSON, and let the engine validate it.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from hab.engine.actions import Action
from hab.mcp_server.tools.equity import equity
from hab.mcp_server.tools.gto_lookup import gto_lookup
from hab.mcp_server.tools.hand_search import hand_history_search
from hab.mcp_server.tools.notes import note_manager
from hab.mcp_server.tools.opponent_db import opponent_database_query
from hab.mcp_server.tools.pot_odds import pot_odds
from hab.mcp_server.tools.range_analyzer import range_analyzer
from hab.orchestrator.action_parser import parse_action_lenient

OPENROUTER_CHAT_COMPLETIONS_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterAgentError(RuntimeError):
    pass


class OpenRouterNoOutput(OpenRouterAgentError):
    pass


class OpenRouterBadAction(OpenRouterAgentError):
    pass


def _json_dumps(data: Any, *, max_chars: int = 12000) -> str:
    text = json.dumps(data, indent=2, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... <truncated>"


TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "equity_calculator",
            "description": "Monte Carlo Hold'em equity vs an opponent range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "my_cards": {"type": "array", "items": {"type": "string"}},
                    "board": {"type": "array", "items": {"type": "string"}},
                    "opponent_range": {"type": "string", "default": "any_two"},
                    "num_opponents": {"type": "integer", "default": 1},
                    "simulations": {"type": "integer", "default": 3000},
                },
                "required": ["my_cards"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pot_odds_calculator",
            "description": "Pot odds, call EV, and bluff breakeven math.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pot": {"type": "number"},
                    "bet_to_call": {"type": "number"},
                    "my_equity": {"type": "number"},
                    "bluff_size": {"type": "number"},
                    "fold_equity": {"type": "number"},
                },
                "required": ["pot", "bet_to_call"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "gto_lookup",
            "description": "Preflop chart lookup; returns engine-legal action hints.",
            "parameters": {
                "type": "object",
                "properties": {
                    "position_scenario": {"type": "string"},
                    "action_sequence": {"type": "string"},
                    "my_cards": {"type": "array", "items": {"type": "string"}},
                    "stack_depth_bb": {"type": "integer", "default": 100},
                },
                "required": ["position_scenario", "my_cards"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "range_analyzer",
            "description": "Estimate opponent range from observed actions and stats.",
            "parameters": {
                "type": "object",
                "properties": {
                    "opponent_id": {"type": "string"},
                    "action_sequence": {"type": "array", "items": {"type": "object"}},
                    "board": {"type": "array", "items": {"type": "string"}},
                    "position": {"type": "string"},
                    "stack_depth_bb": {"type": "integer", "default": 100},
                    "observed_vpip": {"type": "number"},
                },
                "required": ["opponent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "opponent_database_query",
            "description": "Aggregate opponent VPIP/PFR/3-bet/AF/WTSD from observed hands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "opponent_id": {"type": "string"},
                    "filters": {"type": "object"},
                },
                "required": ["opponent_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "hand_history_search",
            "description": "Keyword search over public recent hand history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "opponent_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "note_manager",
            "description": "Read, append, or list private opponent notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read", "append", "list"]},
                    "opponent_id": {"type": "string"},
                    "observation_type": {"type": "string"},
                    "content": {"type": "string"},
                    "hand_id": {"type": "string"},
                },
                "required": ["action", "opponent_id"],
            },
        },
    },
]


class OpenRouterPersistentAgent:
    def __init__(
        self,
        *,
        player_id: str,
        model: str,
        api_key: str,
        http_client: httpx.AsyncClient | None = None,
        max_tool_rounds: int = 4,
    ):
        self.player_id = player_id
        self.model = model
        self.api_key = api_key
        self.max_tool_rounds = max_tool_rounds
        self._client = http_client or httpx.AsyncClient(
            timeout=180.0,
            trust_env=False,
            transport=httpx.AsyncHTTPTransport(retries=3),
        )
        self._owns_client = http_client is None
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": self._system_prompt(),
            }
        ]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def decide(
        self,
        *,
        workspace: Path,
        hand_id: str,
        effective_timeout_sec: float,
    ) -> tuple[Action, dict[str, Any]]:
        view = json.loads((workspace / "game_view" / "current_state.json").read_text())
        hole = json.loads((workspace / "game_view" / "hole_cards.json").read_text())
        self.messages.append({
            "role": "user",
            "content": self._decision_prompt(
                hand_id=hand_id,
                view=view,
                hole=hole,
                effective_timeout_sec=effective_timeout_sec,
            ),
        })
        self._trim_messages()

        tool_calls_used: list[str] = []
        finish_reason: str | None = None
        content = ""
        for _ in range(self.max_tool_rounds + 1):
            message, finish_reason = await self._chat()
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                self.messages.append({
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls,
                })
                for call in tool_calls:
                    name, result = self._execute_tool_call(call, workspace)
                    tool_calls_used.append(name)
                    self.messages.append({
                        "role": "tool",
                        "tool_call_id": call.get("id") or name,
                        "name": name,
                        "content": _json_dumps(result),
                    })
                continue

            content = (message.get("content") or "").strip()
            self.messages.append({"role": "assistant", "content": content})
            break

        if not content:
            raise OpenRouterNoOutput("model returned no final action")

        try:
            action = parse_action_lenient(content, hand_id)
        except Exception as exc:  # noqa: BLE001
            raise OpenRouterBadAction(str(exc)) from exc

        if not action.tool_calls_used and tool_calls_used:
            action = action.model_copy(update={"tool_calls_used": tool_calls_used})
        (workspace / "actions" / "action.json").write_text(
            action.model_dump_json(indent=2) + "\n"
        )
        return action, {
            "finish_reason": finish_reason,
            "final_content": content,
            "tool_calls_used": tool_calls_used,
            "mcp_tool_call_count": len(tool_calls_used),
            "write_tool_call_count": 0,
            "permission_error_count": 0,
            "api_runtime": "openrouter",
        }

    async def _chat(self) -> tuple[dict[str, Any], str | None]:
        response = await self._client.post(
            OPENROUTER_CHAT_COMPLETIONS_URL,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://github.com/holdem-agent-bench",
                "X-Title": "HoldemAgentBench",
                "content-type": "application/json",
            },
            json={
                "model": self.model,
                "messages": self.messages,
                "tools": TOOL_SCHEMAS,
                "tool_choice": "auto",
                "temperature": 0.2,
                "max_tokens": 1200,
            },
        )
        data = response.json()
        if "choices" not in data:
            raise OpenRouterAgentError(f"OpenRouter returned no choices: {data}")
        choice = data["choices"][0]
        return choice.get("message") or {}, choice.get("finish_reason")

    def _execute_tool_call(self, call: dict[str, Any], workspace: Path) -> tuple[str, dict[str, Any]]:
        fn = call.get("function") or {}
        name = fn.get("name") or call.get("name") or "unknown"
        raw_args = fn.get("arguments") or call.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except Exception as exc:  # noqa: BLE001
            return name, {"error": f"invalid tool arguments: {exc}"}

        session_dir = workspace.parent.parent
        try:
            if name == "equity_calculator":
                result = equity(
                    my_cards=args["my_cards"],
                    board=args.get("board"),
                    opponent_range=args.get("opponent_range", "random"),
                    num_opponents=args.get("num_opponents", 1),
                    simulations=args.get("simulations", 3000),
                )
            elif name == "pot_odds_calculator":
                result = pot_odds(
                    pot=args["pot"],
                    bet_to_call=args["bet_to_call"],
                    my_equity=args.get("my_equity"),
                    bluff_size=args.get("bluff_size"),
                    fold_equity=args.get("fold_equity"),
                )
            elif name == "gto_lookup":
                result = gto_lookup(
                    position_scenario=args["position_scenario"],
                    action_sequence=args.get("action_sequence", ""),
                    my_cards=args["my_cards"],
                    stack_depth_bb=args.get("stack_depth_bb", 100),
                )
            elif name == "range_analyzer":
                result = range_analyzer(
                    opponent_id=args["opponent_id"],
                    action_sequence=args.get("action_sequence"),
                    board=args.get("board"),
                    position=args.get("position"),
                    stack_depth_bb=args.get("stack_depth_bb", 100),
                    observed_vpip=args.get("observed_vpip"),
                )
            elif name == "opponent_database_query":
                result = opponent_database_query(
                    session_dir=session_dir,
                    opponent_id=args["opponent_id"],
                    filters=args.get("filters") or {},
                )
            elif name == "hand_history_search":
                result = hand_history_search(
                    session_dir=session_dir,
                    query=args.get("query", ""),
                    opponent_id=args.get("opponent_id"),
                    limit=args.get("limit", 5),
                )
            elif name == "note_manager":
                result = note_manager(
                    workspace=workspace,
                    action=args["action"],
                    opponent_id=args["opponent_id"],
                    observation_type=args.get("observation_type"),
                    content=args.get("content"),
                    hand_id=args.get("hand_id"),
                )
            else:
                result = {"error": f"unknown tool: {name}"}
        except Exception as exc:  # noqa: BLE001
            result = {"error": str(exc)}
        return name, result

    def _trim_messages(self) -> None:
        if len(self.messages) <= 48:
            return
        self.messages = [self.messages[0], *self.messages[-47:]]

    def _system_prompt(self) -> str:
        return (
            f"You are {self.player_id}, an autonomous poker agent in "
            "HoldemAgentBench. Your conversation persists across the whole "
            "match. Use tools when they improve the decision. The final answer "
            "for each turn must be exactly one JSON object matching: "
            '{"action":"fold|check|call|raise|all_in","amount":number|null,'
            '"reason":"short explanation","tool_calls_used":["tool_name"]}. '
            "Only choose actions listed in legal_actions. For call use the exact "
            "call amount; for raise use an absolute target within amount_min and "
            "amount_max."
        )

    def _decision_prompt(
        self,
        *,
        hand_id: str,
        view: dict[str, Any],
        hole: dict[str, Any],
        effective_timeout_sec: float,
    ) -> str:
        return (
            f"Decision required for hand_id={hand_id}. "
            f"You have {effective_timeout_sec:.0f}s before a forced fold.\n\n"
            "Current public game view:\n"
            f"{_json_dumps(view)}\n\n"
            "Your private hole cards:\n"
            f"{_json_dumps(hole)}\n\n"
            "Think briefly, call tools if useful, then return only the final "
            "action JSON. Do not include markdown."
        )
