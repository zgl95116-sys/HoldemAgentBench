# Contributing to HoldemAgentBench

Thanks for your interest in HAB! This project is a benchmark for LLM agents at the poker table — contributions are welcome, whether they're new models, MCP tools, scoring methods, or just bug fixes.

## Ways to contribute

- **Report a bug** — open an issue with a minimal repro (preferably a `mock://` matchup so it doesn't need API keys).
- **Add a model** — most models are reachable through OpenRouter and need no code change. If you're adding a new runtime (alongside `claude-code-persistent`, `claude-code`, `openrouter`), open an issue first to discuss.
- **Add an MCP tool** — see `src/hab/mcp_server/` for the existing 7 tools as templates. Each tool needs a unit test under `tests/unit/mcp_server/`.
- **Improve scoring / analytics** — `src/hab/analytics/` houses Elo, BB/100 with bootstrap CI, and Duplicate Poker variance reduction. AIVAT is on the roadmap.
- **Docs** — methodology, harness benchmark notes, examples.

## Development setup

```bash
git clone https://github.com/<your-fork>/HoldemAgentBench.git
cd HoldemAgentBench
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

All 125 tests run offline with no API keys. CI runs the same suite.

## Running a real-model match locally

```bash
export OPENROUTER_API_KEY=sk-or-...
hab init
hab run quickstart --models anthropic/claude-opus-4-7,openai/gpt-5 --hands 20
```

For the default `claude-code-persistent` runtime you also need the `claude` CLI on your PATH and an `ANTHROPIC_API_KEY`. The `openrouter` runtime is the cheap path for debugging.

## Pull request checklist

- [ ] `pytest tests/ -v` passes locally
- [ ] New behavior has a test (unit test preferred; e2e if it touches the orchestrator)
- [ ] No real API keys, session paths, or personal data in the diff
- [ ] No new files in `hab-sessions/` or `official_runs/` committed by accident
- [ ] If your change affects scoring or harness telemetry, update `docs/methodology.md`
- [ ] If you add a CLI flag or behavior, update `README.md`

## Style

- Python 3.11+, type hints encouraged where they aid clarity
- Don't introduce new top-level packages without discussion
- Keep MCP tool surfaces small — one tool, one job
- Avoid hardcoded model lists; pass them through CLI / preset YAML

## Reporting security issues

Please do **not** open a public issue for security problems. Email the maintainers (see `pyproject.toml` authors) or open a private security advisory on GitHub.

## Code of conduct

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).
