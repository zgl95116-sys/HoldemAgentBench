# HoldemAgentBench (HAB) 🃏

> A benchmark where AI agents face off at the poker table — every model plays inside the same Claude Code harness, with the same MCP tools and skills.

[🏆 Live leaderboard](https://holdem-agent-bench.github.io/holdem-agent-bench) · [📊 Methodology](docs/methodology.md) · [🛠 Harness benchmark](docs/harness-benchmark.md) · [📐 Design doc](docs/design/development-plan-v2.2.md)

---

## Why poker, why a harness

Most LLM benchmarks score a model on solitary turns: a math problem, a code patch, a multiple-choice question. Poker is the opposite — multi-turn, partially observable, adversarial, time-bounded, and resistant to memorization. To play well a model has to reason under uncertainty, model an opponent, decide when to use a tool, and stop deliberating before the clock runs out.

HAB makes that game tractable as a benchmark by fixing the **harness** instead of the model. Every player — Claude Opus, GPT-5, Gemini, DeepSeek, Llama, a `mock://` baseline — runs inside the *same* Claude Code agent loop: it reads its `game_view/`, calls the same 7 MCP poker tools, writes its decision to `actions/action.json`, and waits for the next street. What we measure is whether the model can use that harness to win chips. An Anthropic ↔ OpenAI shim lets the OpenRouter catalogue (300+ models) plug into the same loop without any per-model glue code.

## ⚡ Quick start

```bash
# Free, offline — mock players, no API keys
git clone https://github.com/<your-org>/HoldemAgentBench.git
cd HoldemAgentBench
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

hab run quickstart \
  --models mock://always-fold,mock://always-call \
  --hands 100 --output /tmp/hab-test

hab export-run /tmp/hab-test/<session-id> \
  --output official_runs/<session-id>
```

```bash
# Real models — heads-up, OpenRouter + claude CLI
export OPENROUTER_API_KEY=sk-or-...
hab init
hab run quickstart --models anthropic/claude-opus-4-7,openai/gpt-5

# Cheap fast runtime for debugging (skip Claude Code, talk to OpenRouter directly)
hab run quickstart \
  --agent-runtime openrouter \
  --models z-ai/glm-5.1,z-ai/glm-5.0 \
  --clock 30 --bank-tokens 0

# 6-max
hab run daily-bench \
  --models anthropic/claude-opus-4-7,openai/gpt-5,google/gemini-3-pro,deepseek/deepseek-reasoner,meta-llama/llama-4-maverick,x-ai/grok-4
```

## 🏆 Top 5

<!-- LEADERBOARD_START -->
| Rank | Model | Elo | Skill BB/100 | Harness | Hands |
|------|-------|-----|--------------|---------|-------|
| _no eligible runs yet_ | | | | | |
<!-- LEADERBOARD_END -->

## What an agent actually sees

Each turn, the orchestrator drops a fresh snapshot into the player's workspace and waits for them to write back:

```
workspace/p0/
├── game_view/
│   ├── state.json         # stack, pot, hole cards, board, action history
│   ├── legal_actions.json # {fold, call: 200, raise: [400, ..., 1500]}
│   └── clock.json         # remaining shot-clock seconds + bank tokens
├── actions/
│   └── action.json        # the agent writes this — {"action": "raise", "amount": 600}
└── notes/                 # persistent across hands; the agent's own scratchpad
```

The agent reaches into the shared MCP server for anything it can't compute itself:

| Tool | What it does |
|---|---|
| `equity` | Monte-Carlo equity vs. a hand range or specific hand |
| `pot_odds` | Required equity for a call given pot/bet |
| `gto_lookup` | Preflop GTO action chart lookup by position + stack depth |
| `opponent_db` | VPIP/PFR/3-bet stats for the current opponent across this match |
| `range_analyzer` | Build, intersect, and weight hand ranges |
| `hand_search` | Search prior hands by board texture, action line, or villain |
| `note_manager` | Append/read structured notes (read–write `notes/`) |

Four built-in skills (`meta-strategy`, `poker-fundamentals`, `opponent-modeling`, `gto-reference`) ship as Claude Code skill files, so Claude knows when to reach for what.

## What works

| Feature | Status |
|---------|--------|
| HU NLHE engine (pokerkit-backed) | ✅ button rotation, all-in / bust handling |
| 6-max engine | ✅ generic N-player support (2 ≤ N ≤ 9) |
| OpenRouter shim | ✅ Anthropic ↔ OpenAI translation (non-streaming) |
| MCP toolkit (7 tools) | ✅ |
| Skills | ✅ meta-strategy, poker-fundamentals, opponent-modeling, gto-reference |
| Subprocess agent pool | ✅ process-group isolation, env allowlist, timeout-fold |
| Persistent Claude Code runtime | ✅ one long-lived `claude` CLI process per player |
| OpenRouter fast runtime | ✅ optional, non-core, for comparison/debug |
| Mock models | ✅ always-fold, always-call, min-raise-or-call |
| Three-layer scoring | ✅ Raw BB/100 + bootstrap CI · Duplicate Poker Skill BB/100 (when templates present) · Elo |
| Harness telemetry | ✅ per-decision validity, timeout, file-protocol, latency, tool, permission |
| Leaderboard generator | ✅ JSON + README markers + GitHub Actions |
| Official run export | ✅ public `run.json`, per-hand JSON, decision summary, SHA-256 checksums |
| GitHub Pages site | ✅ Alpine.js + plain HTML/CSS, reads `docs/data/leaderboard.json` |
| `hab init` / `hab run` / `hab export-run` | ✅ |
| Streamlit dashboard · `hab submit` · AIVAT · HuggingFace Space | ⏳ later |

## Architecture

`hab` launches a single in-process orchestrator that drives a pokerkit engine and records every decision to `decision_log.jsonl`. The default `claude-code-persistent` runtime starts a local FastAPI shim that translates Anthropic ↔ OpenAI for OpenRouter, exposes the 7-tool MCP poker server over stdio, and keeps **one** Claude Code CLI process alive per player for the whole match. Each turn the orchestrator drops a `game_view/` snapshot, pings the agent, and waits for `actions/action.json` to appear before the shot clock expires. Official runs default Claude Code to `--effort low` so timed poker decisions don't turn into long-form code-agent deliberation; pass `--claude-effort` to change that.

The legacy `claude-code` runtime exists as a fallback (`claude -p` per decision, no persistence). The `openrouter` runtime is a non-core fast path for debugging that bypasses Claude Code entirely.

> Official benchmark runs must not use `--unsafe-agent-permissions`. That flag exists only as a local escape hatch for Claude CLI setups that require `--dangerously-skip-permissions`.

## Layout

```
src/hab/
├── cli/             hab init / hab run / hab export-run / hab version
├── engine/          pokerkit wrapper, state, recorder
├── orchestrator/    lifecycle, agent_pool, workspace_manager, progress
├── shim/            FastAPI server, Anthropic↔OpenAI translator, router
├── mcp_server/      stdio MCP server + 7 tools
├── analytics/       PlayerStats, EloSystem, DuplicatePokerAnalyzer, LeaderboardGenerator
├── presets/         quickstart.yaml, daily-bench.yaml, full-benchmark.yaml
└── templates/skills/ meta-strategy, poker-fundamentals, opponent-modeling, gto-reference

scripts/             update_leaderboard.py
docs/                index.html, methodology.{md,html}, harness-benchmark.md, design/, plans/, data/
.github/workflows/   update-leaderboard.yml
tests/               125 tests, unit + e2e, no API calls
```

## Test

```bash
pytest tests/ -v
```

All tests run offline. Real-model paths are exercised with the OpenRouter shim pointed at recorded fixtures.

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the PR checklist, and how to add a model, MCP tool, or scoring method. By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).

## Credits

- [pokerkit](https://github.com/uoftcprg/pokerkit) — the deterministic engine HAB drives
- [Anthropic Claude Code](https://docs.claude.com/en/docs/claude-code/overview) — the agent harness
- [OpenRouter](https://openrouter.ai) — uniform access to the model catalogue
- [Model Context Protocol](https://modelcontextprotocol.io) — the tool transport

## License

[MIT](LICENSE)
