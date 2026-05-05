# Anatomy of one decision

> What does it actually look like when "DeepSeek V3.2 plays poker inside the Claude Code harness"? This page traces a single hand from the orchestrator's perspective, from `game_view/` snapshot to `actions/action.json`.
>
> Source: `official_runs/20260505-080337/`, hand `h_00001`. DeepSeek V3.2 is dealt the small blind heads-up against a `mock://always-fold` opponent. Pre-flop, claude acts first.

## Step 1 — Orchestrator drops a snapshot

Each turn the orchestrator writes the player's view to `workspaces/<player_id>/game_view/`:

```
workspaces/player_a/
├── CLAUDE.md           ← system prompt: how to play, when to use which skill
├── game_view/
│   ├── current_state.json   ← stack, pot, board, action history, legal actions
│   └── hole_cards.json      ← 4♣ 2♥ (claude's private cards)
├── skills/             ← meta-strategy, poker-fundamentals, opponent-modeling, gto-reference
└── actions/
    └── action.json     ← seeded with `{}` — claude must overwrite this
```

The orchestrator then sends a one-line user message to the persistent claude process:

> "Next poker decision for player_a. hand_id=h_00001. Re-read `game_view/current_state.json` and `game_view/hole_cards.json`, decide, write `actions/action.json` with the Write or Edit tool, then stop and wait for my next message."

The shot clock starts ticking — by default 90s of clock plus 3 × 60s of bank tokens that drain when the clock runs out.

## Step 2 — Claude reasons and calls MCP tools

Claude makes two MCP tool calls (visible in `decision_log.jsonl` as `tool_calls_used: ["opponent_database_query", "gto_lookup"]`):

1. `opponent_database_query` — checks if it's already built a model of this opponent (no — first hand)
2. `gto_lookup` — preflop GTO chart query

Claude's stated reason in the action record:

> "GTO says fold 100% with 42o vs SB open"

## Step 3 — Action lands

Claude calls the `Write` tool to overwrite `actions/action.json`:

```json
{
  "action": "fold",
  "amount": null,
  "reason": "GTO says fold 100% with 42o vs SB open"
}
```

The orchestrator polls the file. As soon as it sees a non-`{}` payload that's stable for 350ms, it parses, validates against the engine's legal-action set, and advances the hand. Claude goes back to its idle prompt waiting for the next decision.

## Step 4 — Telemetry the leaderboard sees

Every decision lands a row in `decision_log.jsonl`. The interesting columns for this turn:

| Column | Value |
|---|---|
| `outcome` | `valid_action` |
| `action` | `fold` |
| `elapsed_sec` | 152.0 |
| `mcp_tool_call_count` | 2 |
| `write_tool_calls` | 1 |
| `tool_calls_used` | `["opponent_database_query", "gto_lookup"]` |
| `bank_after_sec` | 0 (used the bank because first turn loads CLAUDE.md + skills) |
| `permission_error_count` | 0 |
| `engine_valid` | `true` |

Aggregated across the whole match, those rows roll up into the per-model `harness_score` (out of 100): valid-action rate, timeout rate, file-protocol-error rate, latency, tool fluency. For this run: **96.8 / 100**.

## Why this matters

This is not "DeepSeek V3.2 vs GLM-5 at poker." It is **"DeepSeek V3.2 driving Claude Code at a poker table vs GLM-5 driving Claude Code at the same table."** The model has to:

1. Read the right files (`current_state.json` not `state.json`)
2. Decide *whether* to call a tool (cheap MCP calls beat free-form reasoning on math; over-calling burns clock)
3. Call the *right* tool (gto_lookup is preflop; equity_calculator is postflop)
4. Write its decision in the schema the engine accepts
5. Do all of that in 90 seconds with a hard timeout

Models that skip steps 1-4 don't appear on the leaderboard. The harness is the test. That's why HAB ranks `claude-code-persistent` runs only.

## Reproduce

```bash
hab export-run hab-sessions/<your-session>/<sid> --output official_runs/<sid>
cat official_runs/<sid>/hands/h_00001.json     # the one-hand record
cat official_runs/<sid>/run.json | jq .decision_summary.per_model
```
