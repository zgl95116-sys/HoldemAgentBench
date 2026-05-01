"""Minimal Rich-based progress display for MVP."""
from __future__ import annotations

import time

from rich.console import Console


class ProgressDisplay:
    def __init__(self, total_hands: int, players: list[str]):
        self.total = total_hands
        self.players = players
        self.console = Console()
        self.start_time = time.time()
        self.hands_done = 0
        self.stacks: dict[str, float] = {}

    def hand_complete(self, hand_id: str, stacks: dict[str, float]) -> None:
        self.hands_done += 1
        self.stacks = stacks
        elapsed = time.time() - self.start_time
        eta = elapsed / max(self.hands_done, 1) * max(self.total - self.hands_done, 0)
        line = (
            f"[bold]hand {self.hands_done}/{self.total}[/bold]  "
            + " | ".join(f"{p}: {self.stacks.get(p, 0):.1f}" for p in self.players)
            + f"  | elapsed {elapsed:.0f}s eta {eta:.0f}s"
        )
        self.console.print(line)

    def session_done(self) -> None:
        self.console.print(
            f"[green]session complete:[/green] {self.hands_done} hands "
            f"in {time.time() - self.start_time:.1f}s"
        )
