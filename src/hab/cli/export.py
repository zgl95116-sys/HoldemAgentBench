"""Export a completed session into an auditable public run artifact."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer

from hab.orchestrator.decision_metrics import summarize_decisions

RUN_SCHEMA_VERSION = "hab.run.v1"
CHECKSUM_SCHEMA_VERSION = "hab.checksums.v1"

PUBLIC_HAND_KEYS = {
    "hand_id",
    "winner",
    "pot",
    "stack_deltas",
    "showdown_cards",
    "hole_cards",
    "board",
    "action_history",
    "starting_stacks",
    "button",
    "duplicate_template_id",
    "duplicate_rotation",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_bytes(data: Any) -> bytes:
    return (json.dumps(data, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _summary_value(summary: dict[str, Any], key: str, default: Any = None) -> Any:
    value = summary.get(key)
    return default if value is None else value


def sanitize_public_hand(hand: dict[str, Any]) -> dict[str, Any]:
    """Return a public-only hand record safe for leaderboard publication."""
    public = {k: v for k, v in hand.items() if k in PUBLIC_HAND_KEYS}
    showdown_cards = public.get("showdown_cards") or {}
    if not isinstance(showdown_cards, dict):
        showdown_cards = {}
    public["showdown_cards"] = showdown_cards
    public["hole_cards"] = showdown_cards
    return public


def build_duplicate_templates(
    hands: list[dict[str, Any]],
    *,
    player_count: int,
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for hand in hands:
        template_id = hand.get("duplicate_template_id")
        if not template_id:
            continue
        groups.setdefault(str(template_id), []).append(hand)

    templates: list[dict[str, Any]] = []
    for template_id, group in groups.items():
        rotations = []
        for hand in sorted(
            group,
            key=lambda h: (
                h.get("duplicate_rotation") is None,
                h.get("duplicate_rotation") or 0,
                h.get("hand_id") or "",
            ),
        ):
            rotations.append({
                "hand_id": hand.get("hand_id"),
                "rotation": hand.get("duplicate_rotation"),
                "button": hand.get("button"),
                "player_chips": hand.get("stack_deltas") or {},
            })

        if player_count and len(rotations) < player_count:
            continue
        templates.append({"template_id": template_id, "rotations": rotations})

    return templates


def build_run_export(session_dir: Path) -> dict[str, Any]:
    summary_path = session_dir / "session_summary.json"
    hands_dir = session_dir / "hands"
    if not summary_path.exists():
        raise FileNotFoundError(f"missing session summary: {summary_path}")
    if not hands_dir.exists():
        raise FileNotFoundError(f"missing hands directory: {hands_dir}")

    summary = _load_json(summary_path)
    hand_paths = sorted(hands_dir.glob("*.json"))
    hands = [sanitize_public_hand(_load_json(p)) for p in hand_paths]
    decisions = _load_jsonl(session_dir / "decision_log.jsonl")
    players = summary.get("players") or {}
    duplicate_templates = build_duplicate_templates(
        hands,
        player_count=len(players),
    )
    duplicate_enabled = bool(
        summary.get("duplicate_templates_enabled") or duplicate_templates
    )
    agent_security = summary.get("agent_security") or {
        "environment": "allowlist",
        "unsafe_permissions": False,
        "filesystem_sandbox": "not_recorded",
    }

    return {
        "schema_version": RUN_SCHEMA_VERSION,
        "session_id": summary.get("session_id") or session_dir.name,
        "exported_at": _utc_now(),
        "ended_at": summary.get("ended_at"),
        "players": players,
        "hands_target": _summary_value(summary, "hands_target", len(hands)),
        "hands_played": _summary_value(summary, "hands_played", len(hands)),
        "hands_recorded": len(hands),
        "small_blind": _summary_value(summary, "small_blind", 1.0),
        "big_blind": _summary_value(summary, "big_blind", 2.0),
        "starting_stack": summary.get("starting_stack"),
        "final_stacks": summary.get("final_stacks") or {},
        "duplicate_mode": (
            summary.get("duplicate_mode")
            or ("template_rotation" if duplicate_templates else None)
        ),
        "agent_runtime": summary.get("agent_runtime", "claude-code-persistent"),
        "duplicate_templates_enabled": duplicate_enabled,
        "chip_accounting": (
            summary.get("chip_accounting")
            or ("duplicate_rebuy_net" if duplicate_templates else "continuous_stack")
        ),
        "agent_security": agent_security,
        "duplicate_templates": duplicate_templates,
        "decisions_recorded": len(decisions),
        "decision_summary": (
            summary.get("decision_summary")
            if summary.get("decision_summary", {}).get("decisions") == len(decisions)
            else summarize_decisions(decisions)
        ),
        "privacy": {
            "contains_private_workspaces": False,
            "hole_cards_policy": "only showdown-public cards are exported",
        },
        "decisions": decisions,
        "hands": hands,
    }


def _write_export_files(export: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    hands_dir = output_dir / "hands"
    hands_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, str]] = []

    run_bytes = _json_bytes(export)
    run_path = output_dir / "run.json"
    run_path.write_bytes(run_bytes)
    files.append({"path": "run.json", "sha256": _sha256(run_bytes)})

    for stale in hands_dir.glob("*.json"):
        stale.unlink()

    for hand in export["hands"]:
        hand_id = hand.get("hand_id")
        if not hand_id:
            continue
        rel = Path("hands") / f"{hand_id}.json"
        hand_bytes = _json_bytes(hand)
        (output_dir / rel).write_bytes(hand_bytes)
        files.append({"path": rel.as_posix(), "sha256": _sha256(hand_bytes)})

    manifest = {
        "schema_version": CHECKSUM_SCHEMA_VERSION,
        "session_id": export["session_id"],
        "generated_at": _utc_now(),
        "algorithm": "sha256",
        "files": files,
    }
    (output_dir / "checksums.json").write_bytes(_json_bytes(manifest))
    return manifest


def write_run_export(session_dir: Path, output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    export = build_run_export(session_dir)
    manifest = _write_export_files(export, output_dir)
    return export, manifest


def verify_export(output_dir: Path) -> list[str]:
    manifest_path = output_dir / "checksums.json"
    if not manifest_path.exists():
        return [f"missing checksum manifest: {manifest_path}"]
    manifest = _load_json(manifest_path)
    errors: list[str] = []
    for entry in manifest.get("files") or []:
        rel = entry.get("path")
        expected = entry.get("sha256")
        if not rel or not expected:
            errors.append(f"invalid manifest entry: {entry}")
            continue
        path = output_dir / rel
        if not path.exists():
            errors.append(f"missing file: {rel}")
            continue
        actual = _sha256(path.read_bytes())
        if actual != expected:
            errors.append(f"checksum mismatch: {rel}")
    return errors


def export_command(
    session_dir: Path = typer.Argument(..., help="Completed HAB session directory."),
    output: Path = typer.Option(None, "--output", "-o", help="Export directory."),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing export directory."),
):
    session_dir = session_dir.expanduser()
    if not session_dir.exists():
        raise typer.BadParameter(f"session directory does not exist: {session_dir}")
    output_dir = (output or (Path("official_runs") / session_dir.name)).expanduser()
    if output_dir.exists() and any(output_dir.iterdir()) and not force:
        raise typer.BadParameter(
            f"output exists and is not empty: {output_dir}. Pass --force to overwrite."
        )

    export, manifest = write_run_export(session_dir, output_dir)
    errors = verify_export(output_dir)
    if errors:
        for error in errors:
            typer.echo(f"ERROR: {error}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Exported {export['session_id']} to {output_dir}")
    typer.echo(
        f"Hands: {export['hands_recorded']} | "
        f"duplicate templates: {len(export['duplicate_templates'])} | "
        f"checksums: {len(manifest['files'])}"
    )
