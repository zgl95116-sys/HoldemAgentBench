"""Live (real-time) view for `hab run`.

Streams hand-by-hand visuals to stdout as the engine progresses. Optimised for
2-9 player NLHE; the user sees:
  - hand header (button, starting stacks, all hole cards)
  - each street label as it advances
  - each action as soon as the agent submits it
  - "thinking..." indicator while a model is computing
  - winner + final stacks after the hand
"""
from __future__ import annotations

import time
from typing import Any

from rich.console import Console
from rich.table import Table
from rich.text import Text

from hab.cli.view import cards, format_action, street_label
from hab.engine.actions import Action
from hab.engine.game_master import Event


class LiveDisplay:
    def __init__(self, players: list[str], hands_target: int):
        self.players = players
        self.hands_target = hands_target
        self.console = Console()
        self.start_time = time.time()
        self.hands_done = 0
        self.last_street: str | None = None
        self.current_hand_id: str | None = None
        self._board_shown_for_street: set[str] = set()
        self._think_status = None
        self._think_started_at: float | None = None

    # ---------- per-hand boundaries ----------

    def hand_start(self, ev: Event) -> None:
        self.current_hand_id = ev.hand_id
        self.last_street = None
        self._board_shown_for_street = set()
        button = ev.payload.get("button") if ev.payload else None
        # Header
        header = Text()
        header.append(f"Hand {ev.hand_id}", style="bold cyan")
        if button:
            header.append("  ·  button: ", style="dim")
            header.append(button, style="bold")
        self.console.rule(header)

    def show_starting_table(self, ev: Event) -> None:
        """Called on first action_needed of a hand, when we have hole cards info."""
        if not ev.game_view:
            return
        gv = ev.game_view
        t = Table(show_header=True, header_style="bold", padding=(0, 1))
        t.add_column("player")
        t.add_column("stack", justify="right")
        for p in self.players:
            stack = gv.stacks.root.get(p, 0)
            marker = ""
            if p == ev.player_id:
                marker = "  ← to act"
            t.add_row(f"[bold]{p}[/]", f"{stack:g}{marker}")
        self.console.print(t)

    # ---------- per-decision events ----------

    def action_needed(self, ev: Event, hole_cards_visible: list[str] | None = None) -> None:
        """Called right before the orchestrator dispatches the agent.

        Shows the street boundary if we just transitioned, the player's own hole
        cards, and a 'thinking...' indicator.
        """
        gv = ev.game_view
        if gv is None:
            return

        # Street transition
        if self.last_street != gv.street:
            self.last_street = gv.street
            if gv.street == "preflop":
                self.console.print(street_label(gv.street))
            elif gv.street in ("flop", "turn", "river"):
                # Show new board reveal
                self.console.print(street_label(gv.street), Text("  "), cards(gv.board))

        # Visible hole cards (only the acting player's, since they're the one we're about to query)
        hc = ev.hole_cards.cards if ev.hole_cards else []
        thinking = Text()
        thinking.append("  ⏳ ", style="yellow")
        thinking.append(f"{ev.player_id:<10}", style="bold")
        thinking.append(" ")
        thinking.append("thinking", style="dim italic")
        thinking.append("  ")
        thinking.append("hole ", style="dim")
        thinking.append_text(cards(hc))
        thinking.append("   pot ", style="dim")
        thinking.append(f"{gv.pot:g}", style="dim")
        # Show legal actions briefly
        legal_summary = ", ".join(
            la.type if la.type in ("fold", "check") else
            (f"call {la.amount:g}" if la.type == "call"
             else f"raise [{la.amount_min:g}-{la.amount_max:g}]")
            for la in gv.legal_actions
        )
        thinking.append("   legal: ", style="dim")
        thinking.append(legal_summary, style="dim")
        self.console.print(thinking)
        self._think_started_at = time.time()

    def action_taken(self, ev: Event, action: Action, bank_remaining: float | None = None) -> None:
        elapsed = ""
        if self._think_started_at is not None:
            dt = time.time() - self._think_started_at
            elapsed = f"  ({dt:.1f}s)"
            self._think_started_at = None
        line = format_action(ev.player_id or "?", action.action, action.amount)
        if elapsed:
            line.append(elapsed, style="dim")
        if bank_remaining is not None:
            line.append(f"  bank={int(bank_remaining)}s", style="dim cyan")
        self.console.print(line)
        if action.reason and not action.reason.startswith(("invalid_", "engine_", "no_action", "timeout", "claude_binary_missing", "no_output", "bad_json", "missing_state")):
            self.console.print(Text("      └─ ", style="dim") + Text(f"\"{action.reason}\"", style="dim italic"))
        elif action.reason:
            self.console.print(Text("      └─ ", style="dim red") + Text(action.reason, style="dim red italic"))
        if action.tool_calls_used:
            self.console.print(Text("      └─ tools: ", style="dim") + Text(", ".join(action.tool_calls_used), style="cyan dim"))

    def hand_complete(self, ev: Event) -> None:
        self.hands_done += 1
        payload = ev.payload or {}
        winner = payload.get("winner")
        pot = payload.get("pot", 0)
        deltas = payload.get("stack_deltas", {})

        summary = Text()
        summary.append("→ winner: ", style="bold")
        summary.append(str(winner), style="bold green")
        summary.append(f"   pot ", style="dim")
        summary.append(f"{pot:g}", style="bold")
        summary.append("   ")
        for p in self.players:
            d = deltas.get(p, 0)
            style = "green" if d > 0 else ("red" if d < 0 else "dim")
            summary.append(f"{p}:", style="dim")
            summary.append(f"{d:+g}  ", style=style)
        self.console.print(summary)

        # Periodic running progress
        elapsed = time.time() - self.start_time
        eta = elapsed / max(self.hands_done, 1) * max(self.hands_target - self.hands_done, 0)
        progress = Text()
        progress.append(f"   [{self.hands_done}/{self.hands_target}]", style="dim")
        progress.append(f"  elapsed {elapsed:.0f}s eta {eta:.0f}s", style="dim")
        self.console.print(progress)
        self.console.print()

    def session_done(self) -> None:
        self.console.print(
            f"[green]session complete:[/green] {self.hands_done} hands "
            f"in {time.time() - self.start_time:.1f}s"
        )
