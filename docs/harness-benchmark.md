# HoldemAgentBench Harness Benchmark

**Status:** methodology v1.1  
**Last updated:** 2026-04-29

HoldemAgentBench evaluates full poker agents, not only poker answers. A model must
run inside an agent workspace, read private game state, use optional MCP poker
tools, produce an action file, and survive the same permission and timeout rules
as every other model. The poker table determines who wins chips; the harness
telemetry explains whether the agent environment itself was reliable.

## Evaluation Loop

For each decision:

1. The engine writes `game_view/current_state.json` and
   `game_view/hole_cards.json` into the acting player's workspace.
2. The agent subprocess is launched or resumed with the same per-player session
   id, a minimal environment allowlist, and a fixed tool allowlist.
3. The agent may read state, call MCP poker tools, update notes, and write
   `actions/action.json` with `Write` or `Edit`.
4. The orchestrator parses the action file and submits the action to the poker
   engine.
5. The engine validates legality. Invalid output falls back to check when
   possible, otherwise fold.
6. A public decision record is appended to `decision_log.jsonl`.

HAB currently supports three runtimes:

| Runtime | Use case | How it works |
|---------|----------|--------------|
| `mock` | Offline tests | Deterministic local strategies, no API calls |
| `claude-code-persistent` | Default/core benchmark | One long-lived Claude Code CLI process per player, MCP over stdio, action file written by Claude Code |
| `claude-code` | Legacy compatibility | Per-decision `claude -p` subprocess, persistent Claude session id, MCP over stdio |
| `openrouter` | Non-core fast comparison/debugging | One persistent chat agent per player, OpenRouter Chat Completions, tools executed in-process |

The default `claude-code-persistent` path uses Claude CLI as the execution shell
while the local shim forces each player request to the configured OpenRouter
model. Each player gets one real interactive Claude Code process for the whole
match. The orchestrator sends the next poker decision into that process and waits
for Claude Code to write `actions/action.json`. Official runs default to
`--claude-effort low`; the effort level is printed in run metadata and should be
kept consistent when comparing models.

The legacy `claude-code` path remains available for comparison, but it pays
Claude CLI startup/resume overhead on every decision. The `openrouter` path is
kept as a fast non-core diagnostic runtime; it does not evaluate Claude Code as
the agent shell.

## Scores

HAB exposes two families of metrics:

| Metric family | Purpose | Main fields |
|---------------|---------|-------------|
| Poker score | Measures poker outcome under NLHE rules | Raw BB/100, Duplicate Poker Skill BB/100, Elo |
| Harness score | Measures agent execution reliability | valid action rate, timeout rate, protocol errors, write success, latency, permission errors |

The official leaderboard remains sorted by Elo. Harness score is displayed as an
audit metric so a model that wins a tiny sample by luck but frequently times out
or emits malformed actions is easy to spot.

## Harness Score

Each decision receives a sanitized record with:

- `outcome`: `valid_action`, `invalid_action`, `timeout`, `spawn_failed`,
  `no_output`, `bad_json`, or `error`
- `engine_valid`: whether the action was legal in the current game state
- `elapsed_sec` and `timeout_fraction`
- `write_success`: whether the agent wrote a non-empty action file
- `tool_calls_used`, `mcp_tool_call_count`, and `write_tool_call_count`
- `permission_error_count`
- `unsafe_permissions`: whether the run used the local unsafe escape hatch

The aggregate harness score is a 0-100 reliability score:

```
0.40 * valid_action_rate
+ 0.20 * (1 - timeout_rate)
+ 0.15 * write_success_rate
+ 0.10 * (1 - protocol_error_rate)
+ 0.10 * latency_score
+ 0.05 * (1 - permission_error_rate)
```

`latency_score` is derived from average timeout fraction, so a model that uses
most of its clock every decision is scored lower than a model that acts quickly.

## Time Limits

The timeout is per decision, not per hand or per session. A hand can contain many
decisions across multiple streets, so a 20-hand real-model run can still be slow
when models use most of the clock. Current defaults are conservative for real
Claude Code agents:

- base decision clock: 90 seconds
- time bank: 3 tokens of 60 seconds per player
- hard session-side cap per decision: `decision_timeout_sec`

Timeouts are forced folds and are included in both poker results and harness
metrics.

For quick non-core OpenRouter pilots, use a tighter clock:

```bash
hab run quickstart \
  --agent-runtime openrouter \
  --models z-ai/glm-5.1,z-ai/glm-5.0 \
  --clock 30 --bank-tokens 0 \
  --no-live
```

## Security And Privacy

Official public runs must keep unsafe permissions disabled.

- Agent subprocesses receive only an environment allowlist plus a per-player
  shim token. Host API keys are not inherited.
- The safe Claude Code modes use `acceptEdits` with an explicit tool allowlist.
- The `openrouter` mode does not expose shell/file-edit tools to the model; the
  harness executes only the declared poker tools and writes the final action
  file after parsing.
- `--unsafe-agent-permissions` is a local escape hatch only and maps to Claude
  CLI `--dangerously-skip-permissions`; public leaderboard ingestion rejects it.
- Public hand histories export only showdown-public hole cards. Mucked and
  hidden cards are never exported.
- `hand_history_search` searches sanitized public hand views, not private
  workspaces.

Current limitation: the process runner does not enforce a kernel-level
filesystem sandbox. It relies on environment minimization, tool allowlists, and
public-export policy. A future hardening milestone should add OS/container
sandboxing for untrusted third-party agents.

## Duplicate Poker

`daily-bench` and `full-benchmark` use template rotation. A template is a fixed
card sequence replayed across a full seat rotation. Stacks reset to the preset
starting stack for each rotation, while scores accumulate across the session.
Skill BB/100 is computed only from complete templates.

This separates card luck from technical decisions much better than a single
random deal stream. Raw BB/100 is still exported for transparency.

## Public Artifacts

`hab export-run <session_dir> --output official_runs/<session_id>` writes:

- `run.json`: public session summary, sanitized hands, duplicate templates, and
  decision summaries
- `hands/*.json`: one sanitized hand record per hand
- `checksums.json`: SHA-256 manifest for audit

Private workspaces, raw model text, and hidden cards are not part of official
exports.

## Running A Pilot

```bash
hab run quickstart \
  --models mock://always-fold,mock://always-call \
  --hands 20 \
  --output /tmp/hab-pilot

hab export-run /tmp/hab-pilot/<session-id> \
  --output official_runs/<session-id>

python scripts/update_leaderboard.py --include-unofficial
```

For real models, set `OPENROUTER_API_KEY`, run `hab init`, and use OpenRouter
model ids in `--models`. Treat API keys as short-lived secrets and rotate any
key that was pasted into logs or chat.
