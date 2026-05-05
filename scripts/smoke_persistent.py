#!/usr/bin/env python3
"""Smoke test for the persistent runtime's PTY submission.

Spawns one PersistentClaudeProcess in a throwaway workspace, sends a
prompt asking claude to write a marker file, and reports whether the
file appears within a timeout. Use this to iterate on _send() without
spending money on a 50-hand match.

    OPENROUTER_API_KEY=sk-or-... python scripts/smoke_persistent.py
"""
from __future__ import annotations
import asyncio
import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hab.orchestrator.claude_persistent import (
    PersistentClaudeBadJson,
    PersistentClaudeNoOutput,
    PersistentClaudeProcess,
    PersistentClaudeTimeout,
)
from hab.shim.server import ShimServer


def _bootstrap_smoke_home(
    agent_home: Path, workspace: Path, *, token_placeholder: str
) -> None:
    """Replicate the minimum of AgentPool._bootstrap_agent_home so claude
    can launch in this isolated HOME without blocking on first-run prompts.
    """
    import json as _json

    claude_dir = agent_home / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / "settings.json").write_text(
        _json.dumps(
            {
                "theme": "dark",
                "permissions": {"defaultMode": "auto"},
                "skipAutoPermissionPrompt": True,
                "skipDangerousModePermissionPrompt": True,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )

    # Symlink the host marketplace cache so claude doesn't try to clone.
    host_marketplaces = Path.home() / ".claude" / "plugins" / "marketplaces"
    if host_marketplaces.is_dir():
        plugins_dir = claude_dir / "plugins"
        plugins_dir.mkdir(exist_ok=True)
        target = plugins_dir / "marketplaces"
        if not target.exists():
            try:
                target.symlink_to(host_marketplaces)
            except OSError:
                pass

    trusted_project = {
        "allowedTools": [],
        "mcpContextUris": [],
        "mcpServers": {},
        "enabledMcpjsonServers": [],
        "disabledMcpjsonServers": [],
        "hasTrustDialogAccepted": True,
        "projectOnboardingSeenCount": 1,
        "hasClaudeMdExternalIncludesApproved": False,
        "hasClaudeMdExternalIncludesWarningShown": False,
        "lastGracefulShutdown": True,
    }
    (agent_home / ".claude.json").write_text(
        _json.dumps(
            {
                "numStartups": 0,
                "customApiKeyResponses": {
                    "approved": [token_placeholder],
                    "rejected": [],
                },
                "firstStartTime": "2026-01-01T00:00:00.000Z",
                "opusProMigrationComplete": True,
                "sonnet1m45MigrationComplete": True,
                "migrationVersion": 12,
                "hasCompletedOnboarding": True,
                "lastOnboardingVersion": "2.1.123",
                "lastReleaseNotesSeen": "2.1.123",
                "officialMarketplaceAutoInstallAttempted": True,
                "officialMarketplaceAutoInstalled": False,
                "projects": {
                    str(workspace): trusted_project,
                    str(workspace.resolve()): trusted_project,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


async def main() -> int:
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        print("OPENROUTER_API_KEY required", file=sys.stderr)
        return 2

    workspace = Path("/tmp/hab-smoke-persistent")
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    (workspace / "CLAUDE.md").write_text(
        "You are a smoke-test agent. When prompted, perform the requested "
        "action and stop.\n"
    )
    agent_home = workspace / ".agent_home"
    agent_home.mkdir()

    marker = workspace / "marker.txt"

    shim = ShimServer(openrouter_key=or_key, anthropic_key=None)
    token = shim.register_player("smoke", "deepseek/deepseek-v3.2")
    await shim.start()
    try:
        _bootstrap_smoke_home(agent_home, workspace, token_placeholder=token)
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = shim.base_url
        env["ANTHROPIC_API_KEY"] = token
        env["HOME"] = str(agent_home)

        cmd = [
            "claude", "--effort", "low",
            "--append-system-prompt", (workspace / "CLAUDE.md").read_text(),
            "--permission-mode", "acceptEdits",
            "--disallowedTools", "Bash",
        ]
        agent = PersistentClaudeProcess(
            player_id="smoke",
            workspace=workspace,
            cmd=cmd,
            env=env,
            log_path=workspace / "smoke.log",
        )
        # Bypass ensure_started's default 20s ready-wait — modes other than
        # acceptEdits can take longer to reach the interactive prompt.
        import asyncio as _a
        import fcntl as _fcntl
        import pty as _pty
        master_fd, slave_fd = _pty.openpty()
        agent._master_fd = master_fd
        flags = _fcntl.fcntl(master_fd, _fcntl.F_GETFL)
        _fcntl.fcntl(master_fd, _fcntl.F_SETFL, flags | os.O_NONBLOCK)
        agent.log_path.parent.mkdir(parents=True, exist_ok=True)
        agent.proc = await _a.create_subprocess_exec(
            *cmd,
            cwd=str(workspace),
            env=env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid,
        )
        os.close(slave_fd)
        agent._reader_task = _a.create_task(agent._read_output())
        await agent._wait_for_ready(timeout=60.0)
        await _a.sleep(2.0)

        marker_value = f"hello-{int(time.time())}"
        prompt = (
            f"Use the Write tool to create a file named marker.txt in "
            f"the current working directory. The file content must be "
            f"the literal string {marker_value} with no quotes and no "
            f"extra whitespace. Call only the Write tool. Do not ask "
            f"for clarification first."
        )
        print(f"[smoke] sending prompt of {len(prompt)} bytes")
        t0 = time.time()
        try:
            await agent.request_action(
                prompt=prompt,
                action_path=marker,
                hand_id="smoke-1",
                timeout=180.0,
            )
        except (PersistentClaudeTimeout, PersistentClaudeBadJson, PersistentClaudeNoOutput):
            # BadJson is fine for the smoke test — we don't expect marker.txt
            # to parse as a poker action; we only care whether the file got
            # written at all.
            pass
        finally:
            await agent.close(kill=True)

        elapsed = time.time() - t0
        if marker.exists():
            print(f"[smoke] PASS in {elapsed:.1f}s — marker={marker.read_text()!r}")
            return 0
        print(f"[smoke] FAIL after {elapsed:.1f}s — marker file not written")
        print(f"[smoke] inspect log: {workspace}/smoke.log")
        return 1
    finally:
        await shim.stop()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
