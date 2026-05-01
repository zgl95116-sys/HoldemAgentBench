"""Creates and manages per-player workspace directories."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

CLAUDE_MD_TEMPLATE = """# HAB PokerAgent — {player_id}

You are **{player_id}**, playing in a HoldemAgentBench No-Limit Hold'em match.

## Session model

This is a **persistent session** — your conversation context carries across
all decisions in the match. Every previous hand, your reasoning, what you
saw at showdown, and notes from earlier turns are all retained.

This means:
- You can reference earlier hands directly (e.g. "in h_00007 the villain
  raised the river with what looked like a missed flush draw").
- You don't need to re-read CLAUDE.md or skills/ on every wake — they're
  already in your context.
- You can build a running model of each opponent in your head, or commit
  observations to `notes/opponents/<id>.md` so they survive a crash.

When you're awakened, just read the new game state and act.

## Decision workflow

**Read `skills/meta-strategy/SKILL.md` first.** It tells you how to make every decision.

Other skills available (read on demand):
- `skills/poker-fundamentals/SKILL.md` — pot odds, equity, position, sizing.
- `skills/opponent-modeling/SKILL.md` — VPIP/PFR/AF interpretation, archetypes, note discipline.
- `skills/gto-reference/SKILL.md` — when to query gto_lookup and how to deviate.

## MCP toolkit

The `hab-poker-toolkit` MCP server gives you 7 tools. Use them — they're cheap.

- `equity_calculator(my_cards, board, opponent_range, num_opponents, simulations)`
- `pot_odds_calculator(pot, bet_to_call, my_equity)`
- `gto_lookup(position_scenario, action_sequence, my_cards)`
- `opponent_database_query(opponent_id)` — VPIP/PFR/3-bet/AF/WTSD
- `range_analyzer(opponent_id, observed_vpip)` — implied range
- `hand_history_search(query, opponent_id)` — recent similar spots
- `note_manager(action, opponent_id, content, hand_id)` — read/append your private notes

## Output

Write your decision to `actions/action.json`. The exact schema is in
`skills/meta-strategy/SKILL.md`. Hard rules:

- Use the Claude Code **Write** or **Edit** tool for `actions/action.json`.
  Do **not** use Bash redirection, `tee`, Python scripts, or shell heredocs to
  write the action file; those are intentionally not allowed in official runs.
- Action type must match one of `legal_actions` in `game_view/current_state.json`.
- For `call`: amount = the increment from `legal_actions`.
- For `raise`: amount ∈ `[amount_min, amount_max]` (absolute "raise to" target).
- For `fold`/`check`: amount = null.

## ⏱ Shot clock (this is real)

You play with a **90-second base clock per decision** plus a personal **time
bank** of 3 × 60-second tokens (180s total) for the whole session. The prompt
that wakes you tells you exactly how much time you have for *this* decision.

- Easy spots (clear fold, GTO open, obvious call): aim for **< 60 seconds**.
  One quick `gto_lookup` is enough.
- Standard spots: **< 90 seconds**. `equity_calculator` + one other tool.
- Hard spots (deep stack, big pot, river decision): you can spend bank time,
  but it's borrowed — once it's gone every decision must finish in 90s flat.

Spending time bank is a real cost. Don't tank a routine fold — save the bank
for genuinely thin decisions. After the bank empties, anything > 90s = forced
fold.

## Memory (persistent)

- `notes/opponents/<opponent_id>.md` — your opponent reads (use `note_manager`)
- `notes/strategy.md` — your own reflections

These persist across decisions. Update them when you learn something.

## Honesty

Always set `tool_calls_used` to the *actual* tools you invoked. We log this for analysis.
"""


class WorkspaceManager:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir
        self.workspaces_dir = session_dir / "workspaces"
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def create(self, player_id: str, model: str, enable_mcp: bool = True) -> Path:
        ws = self.workspaces_dir / player_id
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "game_view").mkdir(exist_ok=True)
        (ws / "actions").mkdir(exist_ok=True)
        (ws / "notes").mkdir(exist_ok=True)
        (ws / "notes" / "opponents").mkdir(exist_ok=True)
        (ws / "logs").mkdir(exist_ok=True)
        (ws / ".claude").mkdir(exist_ok=True)
        # Copy skills
        skills_src = Path(__file__).resolve().parent.parent / "templates" / "skills"
        skills_dst = ws / "skills"
        if skills_src.exists() and not skills_dst.exists():
            shutil.copytree(skills_src, skills_dst)
        claude_md = ws / "CLAUDE.md"
        if not claude_md.exists():
            claude_md.write_text(CLAUDE_MD_TEMPLATE.format(player_id=player_id))
        if enable_mcp:
            mcp_cfg_path = ws / ".claude" / "mcp_servers.json"
            mcp_cfg_path.write_text(json.dumps({
                "mcpServers": {
                    "hab-poker-toolkit": {
                        "command": sys.executable,
                        "args": ["-m", "hab.mcp_server.server"],
                        "env": {
                            "PLAYER_ID": player_id,
                            "HAB_SESSION_DIR": str(self.session_dir),
                        },
                    }
                }
            }, indent=2))
        return ws

    def cleanup(self) -> None:
        if self.workspaces_dir.exists():
            shutil.rmtree(self.workspaces_dir, ignore_errors=True)
