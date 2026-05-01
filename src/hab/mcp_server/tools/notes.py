"""note_manager: read/append/list opponent notes per player workspace.

Files live at <workspace>/notes/opponents/<opponent_id>.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


def _opp_file(workspace: Path, opponent_id: str) -> Path:
    safe = "".join(c for c in opponent_id if c.isalnum() or c in "_-")
    return workspace / "notes" / "opponents" / f"{safe}.md"


def note_manager(
    workspace: Path,
    action: str,
    opponent_id: str,
    observation_type: str | None = None,
    content: str | None = None,
    hand_id: str | None = None,
) -> dict:
    """action ∈ {"read", "append", "list"}."""
    notes_dir = workspace / "notes" / "opponents"
    notes_dir.mkdir(parents=True, exist_ok=True)

    if action == "list":
        files = sorted(p.stem for p in notes_dir.glob("*.md"))
        return {"opponents": files}

    if action == "read":
        p = _opp_file(workspace, opponent_id)
        if not p.exists():
            return {"opponent_id": opponent_id, "content": "", "exists": False}
        return {"opponent_id": opponent_id, "content": p.read_text(), "exists": True}

    if action == "append":
        if not content:
            return {"error": "content required for append"}
        p = _opp_file(workspace, opponent_id)
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        header_parts = [f"## {ts}"]
        if hand_id:
            header_parts.append(f"hand:{hand_id}")
        if observation_type:
            header_parts.append(f"type:{observation_type}")
        block = " - ".join(header_parts) + "\n\n" + content.strip() + "\n\n"
        with p.open("a") as f:
            f.write(block)
        return {"appended_to": str(p.relative_to(workspace)), "bytes": len(block)}

    return {"error": f"unknown action: {action}"}
