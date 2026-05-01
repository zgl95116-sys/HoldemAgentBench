"""HAB MCP server: exposes the 7 poker tools over stdio MCP transport.

Each agent's mcp_servers.json points at this module. The server reads
PLAYER_ID and SESSION_DIR from the environment to scope reads/writes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions

from hab.mcp_server.tools.equity import equity
from hab.mcp_server.tools.gto_lookup import gto_lookup
from hab.mcp_server.tools.hand_search import hand_history_search
from hab.mcp_server.tools.notes import note_manager
from hab.mcp_server.tools.opponent_db import opponent_database_query
from hab.mcp_server.tools.pot_odds import pot_odds
from hab.mcp_server.tools.range_analyzer import range_analyzer

logger = logging.getLogger(__name__)


def _workspace() -> Path:
    """Caller's workspace = the cwd of the spawned claude process,
    which is the player workspace dir."""
    return Path.cwd()


def _session_dir() -> Path:
    """Session dir = parent-of-parent of the workspace (workspaces/<pid>/.. = sessions/<id>)."""
    env = os.environ.get("HAB_SESSION_DIR")
    if env:
        return Path(env)
    # Workspace is <session>/workspaces/<player_id>
    return _workspace().parent.parent


server = Server("hab-poker-toolkit")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="equity_calculator",
            description=(
                "Monte Carlo Hold'em equity vs opponent range.\n\n"
                "opponent_range accepts:\n"
                "  - 'any_two' / 'random' (vs random hand)\n"
                "  - Range strings: 'AA,KK,AKs', 'TT+,AJs+,KQo'\n"
                "  - Named presets: 'HU_SB_open', 'HU_BB_3bet', 'tight', 'loose', "
                "'value_only', 'polarized_river_bet', '6M_BTN_open' etc.\n\n"
                "ALWAYS prefer a tight estimated range over 'any_two' once the "
                "opponent has shown action. Equity vs random is misleading."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "my_cards": {"type": "array", "items": {"type": "string"}, "description": "e.g. ['As', 'Kh']"},
                    "board": {"type": "array", "items": {"type": "string"}, "default": []},
                    "opponent_range": {
                        "type": "string",
                        "default": "any_two",
                        "description": "Range string or preset name. Use range_analyzer first for an opponent-specific estimate."
                    },
                    "num_opponents": {"type": "integer", "default": 1},
                    "simulations": {"type": "integer", "default": 3000},
                },
                "required": ["my_cards"],
            },
        ),
        types.Tool(
            name="pot_odds_calculator",
            description=(
                "Calling math (pot odds, EV, call/fold verdict) AND optional "
                "bluffing math (breakeven fold equity, bluff EV)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "pot": {"type": "number"},
                    "bet_to_call": {"type": "number"},
                    "my_equity": {"type": "number", "description": "Your equity (0-1). Required for verdict."},
                    "bluff_size": {"type": "number", "description": "If you're considering a bluff, the size you'd bet."},
                    "fold_equity": {"type": "number", "description": "Estimated probability villain folds to your bluff (0-1)."},
                },
                "required": ["pot", "bet_to_call"],
            },
        ),
        types.Tool(
            name="note_manager",
            description="Read/append/list opponent notes (persisted in this player's workspace).",
            inputSchema={
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
        ),
        types.Tool(
            name="opponent_database_query",
            description="Aggregate VPIP/PFR/3-bet/AF/WTSD stats from observed hands.",
            inputSchema={
                "type": "object",
                "properties": {
                    "opponent_id": {"type": "string"},
                    "filters": {"type": "object"},
                },
                "required": ["opponent_id"],
            },
        ),
        types.Tool(
            name="hand_history_search",
            description="Keyword search over recent hand history.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "default": ""},
                    "opponent_id": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
            },
        ),
        types.Tool(
            name="range_analyzer",
            description="Estimate opponent's range based on observed VPIP.",
            inputSchema={
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
        ),
        types.Tool(
            name="gto_lookup",
            description=(
                "Preflop chart lookup with solver-ballpark frequencies. Returns mixed strategies "
                "where they exist (e.g. raise 0.55 / fold 0.45). Available scenarios:\n"
                "  - HU_SB_open (button = SB acts first)\n"
                "  - HU_BB_vs_open (returns three_bet/call/fold freqs plus an engine-legal action)\n"
                "  - 6M_UTG_open, 6M_HJ_open, 6M_CO_open, 6M_BTN_open, 6M_SB_open"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "position_scenario": {"type": "string"},
                    "action_sequence": {"type": "string"},
                    "my_cards": {"type": "array", "items": {"type": "string"}},
                    "stack_depth_bb": {"type": "integer", "default": 100},
                },
                "required": ["position_scenario", "my_cards"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    args = arguments or {}
    workspace = _workspace()
    session = _session_dir()

    try:
        if name == "equity_calculator":
            result = equity(
                my_cards=args["my_cards"],
                board=args.get("board"),
                opponent_range=args.get("opponent_range", "random"),
                num_opponents=args.get("num_opponents", 1),
                simulations=args.get("simulations", 5000),
            )
        elif name == "pot_odds_calculator":
            result = pot_odds(
                pot=args["pot"],
                bet_to_call=args["bet_to_call"],
                my_equity=args.get("my_equity"),
                bluff_size=args.get("bluff_size"),
                fold_equity=args.get("fold_equity"),
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
        elif name == "opponent_database_query":
            result = opponent_database_query(
                session_dir=session,
                opponent_id=args["opponent_id"],
                filters=args.get("filters") or {},
            )
        elif name == "hand_history_search":
            result = hand_history_search(
                session_dir=session,
                query=args.get("query", ""),
                opponent_id=args.get("opponent_id"),
                limit=args.get("limit", 5),
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
        elif name == "gto_lookup":
            result = gto_lookup(
                position_scenario=args["position_scenario"],
                action_sequence=args.get("action_sequence", ""),
                my_cards=args["my_cards"],
                stack_depth_bb=args.get("stack_depth_bb", 100),
            )
        else:
            result = {"error": f"unknown tool: {name}"}
    except Exception as e:  # noqa: BLE001
        result = {"error": str(e)}

    return [types.TextContent(type="text", text=json.dumps(result, indent=2))]


async def amain() -> None:
    async with mcp.server.stdio.stdio_server() as (read, write):
        await server.run(
            read,
            write,
            InitializationOptions(
                server_name="hab-poker-toolkit",
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def main() -> None:
    asyncio.run(amain())


if __name__ == "__main__":
    main()
