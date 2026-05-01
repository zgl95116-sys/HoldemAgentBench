"""`hab init`: write ~/.hab/config.yaml interactively."""
from __future__ import annotations

import os
from pathlib import Path

import typer
import yaml


def init_command():
    cfg_dir = Path.home() / ".hab"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.yaml"

    typer.echo("🃏 Welcome to HoldemAgentBench (HAB)!\n")
    typer.echo("We use OpenRouter to access 300+ LLMs with one key.")
    typer.echo("Get yours at: https://openrouter.ai/keys\n")

    or_key = typer.prompt(
        "OpenRouter API key", default=os.environ.get("OPENROUTER_API_KEY", "")
    )
    anth_key = typer.prompt(
        "Anthropic API key (optional, press enter to skip)", default=""
    )
    budget = typer.prompt(
        "Default budget per session (USD)", default=50, type=int
    )
    out_dir = typer.prompt(
        "Output directory", default=str(Path.home() / "hab-sessions")
    )

    config = {
        "providers": {
            "openrouter": {"api_key": or_key},
            "anthropic": {
                "api_key": anth_key,
                "use_for_claude_models": bool(anth_key),
            },
        },
        "defaults": {
            "budget_per_session_usd": budget,
            "output_dir": out_dir,
            "max_concurrent_agents": 4,
            "decision_timeout_sec": 300,
        },
    }
    cfg_path.write_text(yaml.safe_dump(config, sort_keys=False))
    typer.echo(f"\n✅ Configuration saved to {cfg_path}")
    typer.echo("\nRun your first benchmark:")
    typer.echo(
        "  hab run quickstart --models anthropic/claude-opus-4-7,openai/gpt-5\n"
    )
