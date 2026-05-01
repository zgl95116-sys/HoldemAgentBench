"""Shared rendering helpers used by both `hab replay` (offline) and the live
view (`hab run`)."""
from __future__ import annotations

from rich.text import Text

_SUIT_GLYPH = {"s": "♠", "h": "♥", "d": "♦", "c": "♣"}
_SUIT_COLOR = {"s": "white", "c": "white", "h": "red", "d": "red"}

_ACTION_COLOR = {
    "fold": "dim red",
    "check": "yellow",
    "call": "cyan",
    "raise": "bold magenta",
    "bet": "bold magenta",
    "all_in": "bold red",
}
_ACTION_ICON = {
    "fold": "✗",
    "check": "✓",
    "call": "→",
    "raise": "↑",
    "bet": "↑",
    "all_in": "‼",
}


def card(s: str) -> Text:
    if not s or len(s) < 2:
        return Text(s or "?")
    rank, suit = s[0], s[1]
    glyph = _SUIT_GLYPH.get(suit, suit)
    color = _SUIT_COLOR.get(suit, "white")
    return Text(f"{rank}{glyph}", style=f"bold {color} on grey15")


def cards(card_list: list[str]) -> Text:
    if not card_list:
        return Text("—", style="dim")
    out = Text()
    for i, c in enumerate(card_list):
        if i > 0:
            out.append(" ")
        out.append_text(card(c))
    return out


def format_action(player_id: str, action_str: str, amount: float | None) -> Text:
    color = _ACTION_COLOR.get(action_str, "white")
    icon = _ACTION_ICON.get(action_str, "•")
    parts = Text()
    parts.append(f"  {icon} ", style=color)
    parts.append(f"{player_id:<10}", style="bold")
    parts.append(" ")
    if amount is not None:
        parts.append(f"{action_str} {amount:g}", style=color)
    else:
        parts.append(action_str, style=color)
    return parts


def street_label(street: str) -> Text:
    return Text(street.upper(), style="bold dim")
