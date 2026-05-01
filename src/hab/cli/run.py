"""`hab run <preset>`."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import typer
import yaml

from hab.orchestrator.lifecycle import HABSession, SessionConfig


def _load_preset(name: str) -> dict:
    here = Path(__file__).resolve().parent.parent / "presets" / f"{name}.yaml"
    if not here.exists():
        raise typer.BadParameter(f"unknown preset: {name}")
    return yaml.safe_load(here.read_text())


def _load_user_config() -> dict:
    p = Path.home() / ".hab" / "config.yaml"
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def run_command(
    preset: str = typer.Argument(..., help="preset name, e.g. 'quickstart'"),
    models: str = typer.Option(
        ...,
        help="comma-separated, e.g. anthropic/claude-opus-4-7,openai/gpt-5. "
             "Use mock://always-fold etc. for offline testing.",
    ),
    hands: int = typer.Option(None, help="override preset hands_target"),
    output: Path = typer.Option(None, help="override output dir"),
    seed: int = typer.Option(42, help="rng seed for the engine"),
    live: bool = typer.Option(True, "--live/--no-live", help="Show real-time hand-by-hand visualization"),
    clock: float = typer.Option(90.0, help="base shot-clock seconds per decision"),
    bank_tokens: int = typer.Option(3, help="number of time-bank tokens per player"),
    bank_token_sec: float = typer.Option(60.0, help="seconds per time-bank token"),
    decision_timeout_sec: float = typer.Option(
        None,
        "--decision-timeout-sec",
        help="Hard cap on time per decision (seconds). Defaults to ~/.hab/config.yaml or 300s.",
    ),
    unsafe_agent_permissions: bool = typer.Option(
        False,
        "--unsafe-agent-permissions",
        help="Pass --dangerously-skip-permissions to Claude subprocesses. Not for official benchmarks.",
    ),
    agent_runtime: str = typer.Option(
        "claude-code-persistent",
        "--agent-runtime",
        help=(
            "Agent runtime: claude-code-persistent (one long-lived Claude CLI "
            "per player), claude-code (one-shot claude -p per decision), or "
            "openrouter (persistent fast runtime)."
        ),
    ),
    claude_effort: str = typer.Option(
        "low",
        "--claude-effort",
        help="Claude Code effort for claude-code runtimes: low, medium, high, xhigh, or max.",
    ),
):
    if agent_runtime not in {"claude-code-persistent", "claude-code", "openrouter"}:
        raise typer.BadParameter(
            "agent runtime must be 'claude-code-persistent', 'claude-code', or 'openrouter'"
        )
    if claude_effort not in {"low", "medium", "high", "xhigh", "max"}:
        raise typer.BadParameter("claude effort must be low, medium, high, xhigh, or max")
    preset_data = _load_preset(preset)
    user_cfg = _load_user_config()
    providers = user_cfg.get("providers", {})
    defaults = user_cfg.get("defaults", {})

    or_key = providers.get("openrouter", {}).get("api_key") or os.environ.get(
        "OPENROUTER_API_KEY"
    )
    anth_key = providers.get("anthropic", {}).get("api_key") or os.environ.get(
        "ANTHROPIC_API_KEY"
    )
    out_dir = output or Path(
        defaults.get("output_dir") or (Path.home() / "hab-sessions")
    ).expanduser()

    model_list = [m.strip() for m in models.split(",") if m.strip()]
    if len(model_list) < 2:
        raise typer.BadParameter("Need at least 2 models.")
    if len(model_list) > 9:
        raise typer.BadParameter("Maximum 9 players supported.")

    players = {f"player_{chr(ord('a') + i)}": m for i, m in enumerate(model_list)}
    duplicate_templates = (
        preset_data.get("evaluation", {}).get("variance_reduction") == "duplicate"
    )

    cfg = SessionConfig(
        players=players,
        hands_target=hands or preset_data["session"]["hands_target"],
        small_blind=preset_data["game"]["small_blind"],
        big_blind=preset_data["game"]["big_blind"],
        starting_stack=preset_data["game"]["starting_stack"],
        output_dir=out_dir,
        max_concurrent_agents=defaults.get("max_concurrent_agents", 4),
        decision_timeout_sec=(
            decision_timeout_sec
            if decision_timeout_sec is not None
            else defaults.get("decision_timeout_sec", 300)
        ),
        seed=seed,
        openrouter_key=or_key,
        anthropic_key=anth_key or None,
        live=live,
        decision_clock_sec=clock,
        time_bank_tokens=bank_tokens,
        time_bank_token_sec=bank_token_sec,
        unsafe_skip_permissions=unsafe_agent_permissions,
        duplicate_templates=duplicate_templates,
        agent_runtime=agent_runtime,
        claude_effort=claude_effort,
    )

    needs_real = any(not m.startswith("mock://") for m in model_list)
    if needs_real and not or_key:
        typer.echo(
            "❌ OPENROUTER_API_KEY not set. Run `hab init` or set the env var.",
            err=True,
        )
        raise typer.Exit(2)

    session = HABSession(cfg)
    typer.echo(f"📂 Session dir: {session.session_dir}")
    typer.echo(
        f"   Players: {list(players.items())} | hands={cfg.hands_target} | "
        f"seed={seed} | runtime={cfg.agent_runtime} | effort={cfg.claude_effort}"
    )
    if cfg.duplicate_templates:
        typer.echo("   Duplicate mode: template rotation enabled")
    result = asyncio.run(session.run())
    typer.echo(f"\n✅ Done. Hands played: {result.get('hands_played')}")
    result_label = "Final scores" if cfg.duplicate_templates else "Final stacks"
    typer.echo(f"   {result_label}: {result.get('final_stacks')}")
