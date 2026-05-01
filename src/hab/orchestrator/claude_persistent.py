"""Persistent Claude Code CLI process driven through a PTY.

This runtime keeps the real Claude Code CLI alive for the whole match. The
orchestrator sends each poker decision as a new interactive user message and
waits for Claude Code to write `actions/action.json`.
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
import pty
import re
import signal
import time
from pathlib import Path
from typing import Any

from hab.engine.actions import Action
from hab.orchestrator.action_parser import parse_action_lenient


class PersistentClaudeError(RuntimeError):
    pass


class PersistentClaudeTimeout(PersistentClaudeError):
    pass


class PersistentClaudeNoOutput(PersistentClaudeError):
    pass


class PersistentClaudeBadJson(PersistentClaudeError):
    pass


class PersistentClaudeProcess:
    def __init__(
        self,
        *,
        player_id: str,
        workspace: Path,
        cmd: list[str],
        env: dict[str, str],
        log_path: Path,
    ):
        self.player_id = player_id
        self.workspace = workspace
        self.cmd = cmd
        self.env = env
        self.log_path = log_path
        self.proc: asyncio.subprocess.Process | None = None
        self._master_fd: int | None = None
        self._reader_task: asyncio.Task | None = None
        self._output_bytes = 0
        self._output_tail = ""
        self._ready = False

    async def ensure_started(self) -> None:
        if self.proc and self.proc.returncode is None:
            return
        master_fd, slave_fd = pty.openpty()
        self._master_fd = master_fd
        flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.proc = await asyncio.create_subprocess_exec(
            *self.cmd,
            cwd=str(self.workspace),
            env=self.env,
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            preexec_fn=os.setsid if os.name != "nt" else None,
        )
        os.close(slave_fd)
        self._reader_task = asyncio.create_task(self._read_output())
        await self._wait_for_ready()
        await asyncio.sleep(2.0)

    async def request_action(
        self,
        *,
        prompt: str,
        action_path: Path,
        hand_id: str,
        timeout: float,
    ) -> tuple[Action, dict[str, Any]]:
        await self.ensure_started()
        before_bytes = self._output_bytes
        self._send(prompt)
        raw = await self._wait_for_action_file(action_path, timeout)
        try:
            action = parse_action_lenient(raw, hand_id)
        except Exception as exc:  # noqa: BLE001
            raise PersistentClaudeBadJson(str(exc)) from exc
        return action, {
            "raw": raw,
            "stdout_bytes": self._output_bytes - before_bytes,
            "process_id": self.proc.pid if self.proc else None,
        }

    def _send(self, prompt: str) -> None:
        if self._master_fd is None:
            raise PersistentClaudeError("persistent Claude process not started")
        payload = " ".join(prompt.split()).encode("utf-8")
        for i in range(0, len(payload), 96):
            os.write(self._master_fd, payload[i : i + 96])
            time.sleep(0.01)
        time.sleep(0.15)
        os.write(self._master_fd, b"\r")
        time.sleep(0.75)
        os.write(self._master_fd, b"\r")

    async def _wait_for_ready(self, timeout: float = 20.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc and self.proc.returncode is not None:
                raise PersistentClaudeNoOutput(
                    f"claude exited with code {self.proc.returncode}"
                )
            tail = self._plain_output_tail()
            if (
                "acceptedits" in tail
                or "ctrl+g to edit" in tail
                or "shift+tab to cycle" in tail
            ):
                self._ready = True
                return
            await asyncio.sleep(0.1)
        raise PersistentClaudeNoOutput(
            "claude did not reach interactive prompt before timeout"
        )

    def _plain_output_tail(self) -> str:
        text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", self._output_tail)
        text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
        text = text.replace("\x1b7", "").replace("\x1b8", "")
        text = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", "", text)
        text = text.replace(" ", "")
        return text.lower()

    async def _wait_for_action_file(self, action_path: Path, timeout: float) -> str:
        deadline = time.time() + timeout
        last_text: str | None = None
        last_change = time.time()
        while time.time() < deadline:
            if self.proc and self.proc.returncode is not None:
                raise PersistentClaudeNoOutput(
                    f"claude exited with code {self.proc.returncode}"
                )
            text = action_path.read_text() if action_path.exists() else ""
            stripped = text.strip()
            if stripped and stripped != "{}":
                if text != last_text:
                    last_text = text
                    last_change = time.time()
                elif time.time() - last_change >= 0.35:
                    return text
            await asyncio.sleep(0.15)
        raise PersistentClaudeTimeout(f"timeout after {timeout:.0f}s")

    async def _read_output(self) -> None:
        assert self._master_fd is not None
        with self.log_path.open("ab") as log:
            while True:
                if self.proc and self.proc.returncode is not None:
                    break
                try:
                    chunk = os.read(self._master_fd, 8192)
                except BlockingIOError:
                    await asyncio.sleep(0.05)
                    continue
                except OSError:
                    break
                if not chunk:
                    await asyncio.sleep(0.05)
                    continue
                self._output_bytes += len(chunk)
                self._output_tail = (self._output_tail + chunk.decode(
                    "utf-8",
                    errors="ignore",
                ))[-20000:]
                log.write(chunk)
                log.flush()

    async def close(self, *, kill: bool = False) -> None:
        if self.proc and self.proc.returncode is None:
            try:
                if kill:
                    self._kill()
                else:
                    self._send("/exit")
                    await asyncio.wait_for(self.proc.wait(), timeout=3.0)
            except Exception:
                self._kill()
        if self._reader_task:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reader_task
        if self._master_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._master_fd)
            self._master_fd = None

    def _kill(self) -> None:
        if not self.proc or self.proc.returncode is not None:
            return
        try:
            if os.name == "nt":
                self.proc.kill()
            else:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
