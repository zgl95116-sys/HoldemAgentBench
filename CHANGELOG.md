# Changelog

All notable changes to this project will be documented in this file. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- **Leaderboard now restricted to the `claude-code-persistent` runtime.** Mixing harness-bearing runs with no-harness `openrouter` runs on the same ranking would be unfair (no file-protocol overhead, no skill loading, no shot-clock pressure on the model). The existing 50-hand `openrouter` run moved from `official_runs/` to `comparison_runs/`.
- **Bootstrap eligibility thresholds.** `MIN_HANDS` 5000 → 200, `MIN_SESSIONS` 3 → 1, dropped the hard duplicate-templates requirement (Skill BB/100 stays "—" without templates but Elo + raw BB/100 still rank). Targets in `docs/methodology.md`; will tighten as more submissions land.

### Fixed
- **`claude-code-persistent` runtime is now functional end-to-end.** The headline path advertised in the README ("every model plays inside the same Claude Code harness") had been silently degenerating into 100% timeout-folds against claude CLI v2.1.x. Five layered bugs uncovered and fixed (commits `532ae08`, `dcc8294`, `4fcde07`):
  - `_send` now wraps prompts in bracketed-paste markers (`\x1b[200~ ... \x1b[201~ + \r`); without this, prompts longer than the TUI input width were chopped at line wraps and never submitted.
  - `_wait_for_ready` marker set rewritten — the original needles had embedded spaces but `_plain_output_tail` strips whitespace, so only the `acceptEdits` mode matched by accident; every other permission mode hung at startup. Now matches all four documented modes plus the universal `shift+tabtocycle` hint. Default ready-wait timeout bumped 20s → 60s for cold starts that include marketplace bootstrap.
  - Removed `--bare` from spawn commands. In claude CLI v2.1.128 `--bare` strips the `Write` built-in tool, which makes it impossible for the agent to create `actions/action.json`.
  - Each agent's per-workspace `.agent_home/.claude/plugins/marketplaces/` is now a symlink to the host's marketplace cache; previously claude tried (and silently failed) to git-clone `anthropic/claude-plugins-official` on every spawn.
  - `_build_env` no longer sets both `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN`; the conflict warning was stalling the input prompt at startup.
- Persistent agent now survives a single decision timeout. Replaced the kill+pop on `PersistentClaudeTimeout` and `PersistentClaudeBadJson` with a soft `interrupt()` that sends Ctrl+C plus a status note over the PTY, so the loaded skills and conversation context carry over to the next decision instead of paying a 30-60s cold-start cost.

### Added
- `hab run --decision-timeout-sec` CLI flag to override the per-decision hard cap (was pinned at 300s via `~/.hab/config.yaml`).
- `scripts/smoke_persistent.py` — 30-second diagnostic that drives one real `claude` turn end-to-end (writes a marker file). Use this for fast PTY-protocol iteration without burning a poker match.
- `tests/e2e/test_persistent_runtime.py` — gated end-to-end regression test for the runtime. Skipped by default; enable with `HAB_E2E_CLAUDE=1` plus `OPENROUTER_API_KEY`.
- `docs/plans/2026-05-05-fix-persistent-runtime.md` — the full diagnostic narrative behind the fix.

### Changed
- Session summary now reports `agent_runtime: "mock"` (and `agent_security.permission_mode: "n/a"`) when every player is a `mock://` player. Was misleadingly recording the configured runtime even though no Claude Code process actually launched.
- `hab --version` now works as a top-level flag in addition to the `hab version` subcommand.

## [0.1.0] — 2026-05-01

Initial public release.

- pokerkit-backed engine, 2-9 players
- Three agent runtimes: `claude-code-persistent`, `claude-code`, `openrouter`
- Anthropic ↔ OpenAI shim for OpenRouter (300+ models)
- 7 MCP poker tools (equity, pot odds, GTO lookup, opponent DB, range analyzer, hand search, notes)
- Three-layer scoring (BB/100 with bootstrap CI, Duplicate Poker skill BB/100, Elo)
- Official run export with SHA-256 checksums
- Leaderboard generator + GitHub Pages site
- 128 offline tests
