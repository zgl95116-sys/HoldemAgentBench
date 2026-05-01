"""`hab replay <session_dir>`: pretty-print a session hand by hand."""
from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from hab.cli.view import cards as _cards
from hab.cli.view import format_action as _shared_format_action


def _format_action(entry: dict, players: list[str]) -> Text:
    return _shared_format_action(
        entry.get("player_id", "?"),
        entry.get("action", "?"),
        entry.get("amount"),
    )


def _render_hand(console: Console, hand: dict, players: list[str]) -> None:
    hand_id = hand.get("hand_id", "?")
    button = hand.get("button", "?")
    starting = hand.get("starting_stacks", {})
    hole_all = hand.get("hole_cards", {})
    showdown = hand.get("showdown_cards", {})
    board = hand.get("board", []) or []
    actions = hand.get("action_history", []) or []
    deltas = hand.get("stack_deltas", {})
    winner = hand.get("winner", "?")

    title = f"[bold cyan]Hand {hand_id}[/]  · button: [bold]{button}[/]"
    console.rule(title)

    # Stacks table
    t = Table(show_header=True, header_style="bold", padding=(0, 1))
    t.add_column("player")
    t.add_column("starting", justify="right")
    t.add_column("hole", justify="left")
    t.add_column("Δ", justify="right")
    for p in players:
        delta = deltas.get(p, 0)
        delta_style = "green" if delta > 0 else ("red" if delta < 0 else "dim")
        cards = hole_all.get(p) or showdown.get(p) or []
        t.add_row(
            f"[bold]{p}[/]",
            f"{starting.get(p, 0):g}",
            "" if not cards else None,  # placeholder; we'll add cards via add_row below
        )
    # Re-render properly: typer/rich Table doesn't accept rich Text inline easily for some setups
    # Build manually:
    t = Table(show_header=True, header_style="bold", padding=(0, 1))
    t.add_column("player")
    t.add_column("starting", justify="right")
    t.add_column("hole", justify="left")
    t.add_column("Δ", justify="right")
    for p in players:
        delta = deltas.get(p, 0)
        delta_style = "green" if delta > 0 else ("red" if delta < 0 else "dim")
        cards = hole_all.get(p) or showdown.get(p) or []
        t.add_row(
            f"[bold]{p}[/]",
            f"{starting.get(p, 0):g}",
            _cards(cards),
            Text(f"{delta:+g}", style=delta_style),
        )
    console.print(t)

    # Walk through action history grouped by street
    streets = ["preflop", "flop", "turn", "river", "showdown", "complete"]
    by_street: dict[str, list[dict]] = {s: [] for s in streets}
    for a in actions:
        by_street.setdefault(a.get("street", "preflop"), []).append(a)

    for street in streets:
        if not by_street.get(street):
            continue
        # Show the board reveal at start of each post-preflop street
        if street == "flop":
            console.print(Text("FLOP:  ", style="bold dim"), _cards(board[:3]))
        elif street == "turn":
            console.print(Text("TURN:  ", style="bold dim"), _cards(board[:4]))
        elif street == "river":
            console.print(Text("RIVER: ", style="bold dim"), _cards(board[:5]))
        elif street == "preflop":
            console.print(Text("PREFLOP", style="bold dim"))

        for a in by_street[street]:
            console.print(_format_action(a, players))
            reason = a.get("reason")
            tools = a.get("tool_calls_used") or []
            if reason and not reason.startswith(("invalid_", "engine_", "no_action", "timeout")):
                console.print(Text(f"      └─ ", style="dim") + Text(f"\"{reason}\"", style="dim italic"))
            if tools:
                console.print(Text(f"      └─ tools: ", style="dim") + Text(", ".join(tools), style="cyan dim"))

    pot = hand.get("pot", 0)
    summary = Text()
    summary.append("Winner: ", style="bold")
    summary.append(str(winner), style="bold green")
    summary.append(f"   pot {pot:g}")
    console.print(summary)
    console.print()


def replay_command(
    session_dir: Path = typer.Argument(..., exists=True, file_okay=False, dir_okay=True),
    limit: int = typer.Option(None, help="Only show the first N hands"),
    only: str = typer.Option(None, help="Comma-separated hand IDs to show"),
) -> None:
    """Replay a HAB session hand-by-hand in the terminal."""
    console = Console()

    summary_path = session_dir / "session_summary.json"
    if summary_path.exists():
        summary = json.loads(summary_path.read_text())
        console.rule(f"[bold]{summary.get('session_id', session_dir.name)}[/]")
        meta = Table.grid(padding=(0, 2))
        meta.add_column(style="dim")
        meta.add_column()
        for k in ("hands_target", "hands_played", "final_stacks", "players"):
            if k in summary:
                meta.add_row(k, str(summary[k]))
        console.print(meta)
        console.print()
        players = list(summary.get("players", {}).keys())
    else:
        players = []

    hands_dir = session_dir / "hands"
    files = sorted(hands_dir.glob("h_*.json"))
    if only:
        wanted = {x.strip() for x in only.split(",")}
        files = [f for f in files if f.stem in wanted]
    if limit:
        files = files[:limit]

    for f in files:
        hand = json.loads(f.read_text())
        if not players:
            players = list(hand.get("starting_stacks", {}).keys()) or list(hand.get("stack_deltas", {}).keys())
        _render_hand(console, hand, players)

    if not files:
        console.print("[yellow]no hands found in session[/]")
