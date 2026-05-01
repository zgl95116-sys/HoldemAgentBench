"""Spawns headless Claude Code subprocesses on demand.

Supports a `mock://<strategy>` model URI so we can exercise the orchestrator
without burning API credits. Real models go through `claude -p ...` which the
orchestrator's shim has already redirected via env vars.

Shot-clock + time-bank rules (Triton-style):
  - base clock per decision (default 30s)
  - each player has N time-bank tokens (default 5 × 60s)
  - if a decision overruns the base clock, the excess is deducted from the bank
  - if the bank is empty and the decision would still exceed the base clock,
    we kill the agent and force-fold
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hab.engine.actions import Action
from hab.orchestrator.action_parser import parse_action_lenient
from hab.orchestrator.claude_persistent import (
    PersistentClaudeBadJson,
    PersistentClaudeNoOutput,
    PersistentClaudeProcess,
    PersistentClaudeTimeout,
)
from hab.orchestrator.decision_metrics import DECISION_SCHEMA_VERSION
from hab.orchestrator.openrouter_agent import (
    OpenRouterAgentError,
    OpenRouterBadAction,
    OpenRouterNoOutput,
    OpenRouterPersistentAgent,
)

logger = logging.getLogger(__name__)

_ALLOWED_REAL_AGENT_TOOLS = (
    "Read",
    "Write",
    "Edit",
    "mcp__hab-poker-toolkit__equity_calculator",
    "mcp__hab-poker-toolkit__pot_odds_calculator",
    "mcp__hab-poker-toolkit__note_manager",
    "mcp__hab-poker-toolkit__opponent_database_query",
    "mcp__hab-poker-toolkit__hand_history_search",
    "mcp__hab-poker-toolkit__range_analyzer",
    "mcp__hab-poker-toolkit__gto_lookup",
)


def _fold(reason: str, hand_id: str | None) -> Action:
    return Action(action="fold", hand_id=hand_id, reason=reason, tool_calls_used=[])


_parse_action_lenient = parse_action_lenient


class AgentPool:
    def __init__(
        self,
        shim_url: str,
        max_concurrent: int = 4,
        decision_timeout: float = 300.0,
        claude_binary: str = "claude",
        player_tokens: dict[str, str] | None = None,
        players: list[str] | None = None,
        decision_clock_sec: float = 30.0,
        time_bank_tokens: int = 5,
        time_bank_token_sec: float = 60.0,
        unsafe_skip_permissions: bool = False,
        agent_runtime: str = "claude-code",
        openrouter_key: str | None = None,
        claude_effort: str = "low",
    ):
        self.shim_url = shim_url
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.decision_timeout = decision_timeout
        self.claude_binary = claude_binary
        self.player_tokens = player_tokens or {}
        self.active: dict[str, asyncio.subprocess.Process] = {}
        # Shot-clock + per-player time bank
        self.decision_clock = decision_clock_sec
        self.time_bank_token_sec = time_bank_token_sec
        # Initial bank for everyone (in seconds)
        starting_bank = float(time_bank_tokens) * time_bank_token_sec
        self.bank_remaining: dict[str, float] = {
            p: starting_bank for p in (players or [])
        }
        self._starting_bank = starting_bank
        # Persistent claude sessions: each player gets a stable UUID. First call
        # uses --session-id <uuid>, subsequent calls --resume <uuid>. This gives
        # each agent a continuous context across decisions.
        self.session_ids: dict[str, str] = {
            p: str(uuid.uuid4()) for p in (players or [])
        }
        self.session_started: dict[str, bool] = {}
        self.unsafe_skip_permissions = unsafe_skip_permissions
        self.agent_runtime = agent_runtime
        self.openrouter_key = openrouter_key
        self.claude_effort = claude_effort
        self._openrouter_agents: dict[str, OpenRouterPersistentAgent] = {}
        self._persistent_claude_agents: dict[str, PersistentClaudeProcess] = {}
        self._last_decisions: dict[tuple[str, str], dict[str, Any]] = {}
        self._session_log_offsets: dict[str, int] = {}

    async def request_action(
        self,
        player_id: str,
        model: str,
        workspace: Path,
        hand_id: str,
    ) -> Action:
        if model.startswith("mock://"):
            return await self._mock_action(player_id, model, workspace, hand_id)
        if self.agent_runtime == "openrouter":
            return await self._openrouter_action(player_id, model, workspace, hand_id)
        if self.agent_runtime == "claude-code-persistent":
            return await self._persistent_claude_action(player_id, model, workspace, hand_id)
        return await self._real_action(player_id, model, workspace, hand_id)

    async def _mock_action(
        self,
        player_id: str,
        model: str,
        workspace: Path,
        hand_id: str,
    ) -> Action:
        t_start = time.time()
        record = self._new_decision_record(
            player_id=player_id,
            model=model,
            hand_id=hand_id,
            agent_kind="mock",
            effective_timeout_sec=self.decision_timeout,
        )
        action = await self._mock_action_impl(model, workspace, hand_id)
        self._finish_decision_record(
            workspace=workspace,
            record=record,
            t_start=t_start,
            outcome="valid_action",
            action=action,
            write_success=True,
        )
        return action

    async def _mock_action_impl(self, model: str, workspace: Path, hand_id: str) -> Action:
        strategy = model[len("mock://"):]
        view_path = workspace / "game_view" / "current_state.json"
        if not view_path.exists():
            return _fold("missing_state", hand_id)
        view = json.loads(view_path.read_text())
        legal = view.get("legal_actions", [])

        def _has(t: str) -> dict | None:
            for la in legal:
                if la["type"] == t:
                    return la
            return None

        if strategy == "always-fold":
            if _has("fold"):
                return _fold("mock-strategy", hand_id)
            # When fold isn't legal (rare), prefer check
            if _has("check"):
                return Action(action="check", hand_id=hand_id, tool_calls_used=[])
            return _fold("mock-strategy-fallback", hand_id)
        if strategy == "always-call":
            if (la := _has("check")):
                return Action(action="check", hand_id=hand_id, tool_calls_used=[])
            if (la := _has("call")):
                return Action(action="call", amount=la["amount"], hand_id=hand_id, tool_calls_used=[])
            return _fold("no-call-available", hand_id)
        if strategy == "min-raise-or-call":
            if (la := _has("raise")) and la.get("amount_min") is not None:
                return Action(
                    action="raise",
                    amount=la["amount_min"],
                    hand_id=hand_id,
                    tool_calls_used=[],
                )
            if (la := _has("check")):
                return Action(action="check", hand_id=hand_id, tool_calls_used=[])
            if (la := _has("call")):
                return Action(action="call", amount=la["amount"], hand_id=hand_id, tool_calls_used=[])
            return _fold("nothing-legal", hand_id)
        return _fold(f"unknown-mock:{strategy}", hand_id)

    async def _openrouter_action(
        self,
        player_id: str,
        model: str,
        workspace: Path,
        hand_id: str,
    ) -> Action:
        async with self.semaphore:
            if not self.openrouter_key:
                action = _fold("spawn_failed:missing_openrouter_key", hand_id)
                record = self._new_decision_record(
                    player_id=player_id,
                    model=model,
                    hand_id=hand_id,
                    agent_kind="openrouter",
                    effective_timeout_sec=0.0,
                )
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=time.time(),
                    outcome="spawn_failed",
                    action=action,
                    write_success=False,
                    parse_error="missing_openrouter_key",
                )
                return action

            (workspace / "actions").mkdir(parents=True, exist_ok=True)
            (workspace / "actions" / "action.json").write_text("{}\n")
            bank = self.bank_remaining.get(player_id, self._starting_bank)
            effective_timeout = min(self.decision_clock + bank, self.decision_timeout)
            t_start = time.time()
            record = self._new_decision_record(
                player_id=player_id,
                model=model,
                hand_id=hand_id,
                agent_kind="openrouter",
                effective_timeout_sec=effective_timeout,
                bank_before_sec=bank,
            )
            agent = self._openrouter_agents.get(player_id)
            if agent is None:
                agent = OpenRouterPersistentAgent(
                    player_id=player_id,
                    model=model,
                    api_key=self.openrouter_key,
                )
                self._openrouter_agents[player_id] = agent
            try:
                action, meta = await asyncio.wait_for(
                    agent.decide(
                        workspace=workspace,
                        hand_id=hand_id,
                        effective_timeout_sec=effective_timeout,
                    ),
                    timeout=effective_timeout,
                )
                self._consume_bank(player_id, time.time() - t_start)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="valid_action",
                    action=action,
                    raw=meta.get("final_content"),
                    write_success=True,
                )
                self._apply_openrouter_meta(record, meta)
                return action
            except asyncio.TimeoutError:
                self._consume_bank(player_id, effective_timeout)
                action = _fold(
                    f"timeout (clock+bank exhausted at {effective_timeout:.0f}s)",
                    hand_id,
                )
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="timeout",
                    action=action,
                    write_success=False,
                )
                self._apply_openrouter_meta(record, {})
                return action
            except OpenRouterNoOutput as e:
                self._consume_bank(player_id, time.time() - t_start)
                action = _fold("no_output", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="no_output",
                    action=action,
                    write_success=False,
                    parse_error=str(e),
                )
                self._apply_openrouter_meta(record, {})
                return action
            except OpenRouterBadAction as e:
                self._consume_bank(player_id, time.time() - t_start)
                action = _fold(f"bad_json:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="bad_json",
                    action=action,
                    write_success=False,
                    parse_error=str(e),
                )
                self._apply_openrouter_meta(record, {})
                return action
            except OpenRouterAgentError as e:
                self._consume_bank(player_id, time.time() - t_start)
                action = _fold(f"error:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="error",
                    action=action,
                    write_success=False,
                    parse_error=str(e),
                )
                self._apply_openrouter_meta(record, {})
                return action
            except Exception as e:
                self._consume_bank(player_id, time.time() - t_start)
                action = _fold(f"error:{type(e).__name__}:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="error",
                    action=action,
                    write_success=False,
                    parse_error=f"{type(e).__name__}:{e}",
                )
                self._apply_openrouter_meta(record, {})
                return action

    async def _persistent_claude_action(
        self,
        player_id: str,
        model: str,
        workspace: Path,
        hand_id: str,
    ) -> Action:
        async with self.semaphore:
            (workspace / "actions").mkdir(parents=True, exist_ok=True)
            af = workspace / "actions" / "action.json"
            af.write_text("{}\n")
            bank = self.bank_remaining.get(player_id, self._starting_bank)
            effective_timeout = min(self.decision_clock + bank, self.decision_timeout)
            t_start = time.time()
            record = self._new_decision_record(
                player_id=player_id,
                model=model,
                hand_id=hand_id,
                agent_kind="claude-code-persistent",
                effective_timeout_sec=effective_timeout,
                bank_before_sec=bank,
            )
            agent = self._persistent_claude_agents.get(player_id)
            if agent is None:
                env = self._build_env(player_id, model, workspace)
                session_id = self.session_ids.get(player_id) or str(uuid.uuid4())
                self.session_ids[player_id] = session_id
                record["agent_session_id"] = session_id
                cmd = self._persistent_claude_cmd(
                    workspace=workspace,
                    player_id=player_id,
                    session_id=session_id,
                )
                agent = PersistentClaudeProcess(
                    player_id=player_id,
                    workspace=workspace,
                    cmd=cmd,
                    env=env,
                    log_path=workspace / "logs" / "claude-persistent.log",
                )
                self._persistent_claude_agents[player_id] = agent
            else:
                record["agent_session_id"] = self.session_ids.get(player_id)

            prompt = self._persistent_turn_prompt(
                player_id=player_id,
                hand_id=hand_id,
                effective_timeout=effective_timeout,
                first_turn=not self.session_started.get(player_id),
            )
            self.session_started[player_id] = True
            try:
                action, meta = await agent.request_action(
                    prompt=prompt,
                    action_path=af,
                    hand_id=hand_id,
                    timeout=effective_timeout,
                )
                self._consume_bank(player_id, time.time() - t_start)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="valid_action",
                    action=action,
                    raw=meta.get("raw"),
                    write_success=True,
                )
                record["api_runtime"] = "claude-code-persistent"
                record["persistent_process_id"] = meta.get("process_id")
                return action
            except PersistentClaudeTimeout as e:
                self._consume_bank(player_id, effective_timeout)
                await agent.close(kill=True)
                self._persistent_claude_agents.pop(player_id, None)
                action = _fold(
                    f"timeout (clock+bank exhausted at {effective_timeout:.0f}s)",
                    hand_id,
                )
                raw_timeout = af.read_text() if af.exists() else None
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="timeout",
                    action=action,
                    raw=raw_timeout,
                    write_success=bool(raw_timeout and raw_timeout.strip() not in ("", "{}")),
                    parse_error=str(e),
                )
                record["api_runtime"] = "claude-code-persistent"
                return action
            except PersistentClaudeNoOutput as e:
                self._consume_bank(player_id, time.time() - t_start)
                await agent.close(kill=True)
                self._persistent_claude_agents.pop(player_id, None)
                action = _fold("no_output", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="no_output",
                    action=action,
                    write_success=False,
                    parse_error=str(e),
                )
                record["api_runtime"] = "claude-code-persistent"
                return action
            except PersistentClaudeBadJson as e:
                self._consume_bank(player_id, time.time() - t_start)
                raw_bad = af.read_text() if af.exists() else None
                action = _fold(f"bad_json:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="bad_json",
                    action=action,
                    raw=raw_bad,
                    write_success=bool(raw_bad and raw_bad.strip() not in ("", "{}")),
                    parse_error=str(e),
                )
                record["api_runtime"] = "claude-code-persistent"
                return action

    async def _real_action(
        self,
        player_id: str,
        model: str,
        workspace: Path,
        hand_id: str,
    ) -> Action:
        async with self.semaphore:
            (workspace / "actions").mkdir(parents=True, exist_ok=True)
            af = workspace / "actions" / "action.json"
            af.write_text("{}\n")
            env = self._build_env(player_id, model, workspace)
            # Compute effective timeout: base clock + remaining time bank
            bank = self.bank_remaining.get(player_id, self._starting_bank)
            effective_timeout = min(
                self.decision_clock + bank,
                self.decision_timeout,
            )
            t_start = time.time()
            record = self._new_decision_record(
                player_id=player_id,
                model=model,
                hand_id=hand_id,
                agent_kind="real",
                effective_timeout_sec=effective_timeout,
                bank_before_sec=bank,
            )
            tokens_left = bank / self.time_bank_token_sec if self.time_bank_token_sec > 0 else 0
            time_line = (
                f"⏱ Shot clock: {int(self.decision_clock)}s base + "
                f"{int(bank)}s time bank ({tokens_left:.1f} tokens of "
                f"{int(self.time_bank_token_sec)}s left). "
                f"After {int(effective_timeout)}s total, you will be force-folded."
            )
            if self.session_started.get(player_id):
                # Continuation: model already has CLAUDE.md, skills, prior hands
                # in its context. Just point at the new spot.
                prompt = (
                    f"It's your turn again ({player_id}, hand_id={hand_id}). "
                    f"Re-read game_view/current_state.json and game_view/hole_cards.json "
                    f"for the new spot, decide, write actions/action.json with the Write "
                    f"or Edit tool, then exit. Do not use Bash, shell commands, or "
                    f"nested claude processes. actions/action.json already contains "
                    f"`{{}}`; if using Edit, replace old_string `{{}}`.\n\n"
                    f"{time_line}"
                )
            else:
                prompt = (
                    f"You are {player_id}. Read CLAUDE.md and skills/, then read "
                    f"game_view/current_state.json + game_view/hole_cards.json, "
                    f"decide an action, write actions/action.json with the Write or "
                    f"Edit tool, then exit. Do not use Bash, shell commands, or "
                    f"nested claude processes. actions/action.json already contains "
                    f"`{{}}`; if using Edit, replace old_string `{{}}`. "
                    f"hand_id={hand_id}.\n\n"
                    f"{time_line}\n\n"
                    f"This session will persist across all your future decisions, "
                    f"so any context, opponent reads, or strategy you build now will "
                    f"carry forward. Use notes/ for things you'd rather have written "
                    f"down for future-you."
                )
            mcp_cfg = workspace / ".claude" / "mcp_servers.json"
            session_id = self.session_ids.get(player_id) or str(uuid.uuid4())
            self.session_ids[player_id] = session_id
            record["agent_session_id"] = session_id
            if self.session_started.get(player_id):
                session_args = ["--resume", session_id]
            else:
                session_args = ["--session-id", session_id]
            cmd = [
                self.claude_binary,
                "-p", prompt,
                # --bare skips hooks, LSP, plugin sync, attribution, auto-memory,
                # background prefetches, keychain reads, and CLAUDE.md auto-discovery.
                # We don't need any of those for headless agent invocations.
                "--bare",
                "--effort", self.claude_effort,
                # --bare disables CLAUDE.md auto-discovery; inline its content
                # into the system prompt instead. Only on first call (resume keeps it).
                *(
                    [
                        "--append-system-prompt",
                        (workspace / "CLAUDE.md").read_text(),
                    ]
                    if not self.session_started.get(player_id) and (workspace / "CLAUDE.md").exists()
                    else []
                ),
                *session_args,
            ]
            if self.unsafe_skip_permissions:
                cmd.append("--dangerously-skip-permissions")
            else:
                cmd += [
                    "--permission-mode", "acceptEdits",
                    "--allowedTools", ",".join(_ALLOWED_REAL_AGENT_TOOLS),
                    "--disallowedTools", "Bash",
                ]
            if mcp_cfg.exists():
                cmd += [
                    "--mcp-config", str(mcp_cfg),
                    "--strict-mcp-config",
                ]
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(workspace),
                    env=env,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    preexec_fn=os.setsid if os.name != "nt" else None,
                )
            except FileNotFoundError as e:
                self._archive(workspace, player_id, hand_id, raw=None, parse_error=f"FileNotFoundError:{e}")
                action = _fold(f"spawn_failed:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="spawn_failed",
                    action=action,
                    write_success=False,
                    parse_error=f"FileNotFoundError:{e}",
                )
                return action
            except Exception as e:
                self._archive(workspace, player_id, hand_id, raw=None, parse_error=f"spawn:{type(e).__name__}:{e}")
                action = _fold(f"spawn_failed:{type(e).__name__}:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="spawn_failed",
                    action=action,
                    write_success=False,
                    parse_error=f"spawn:{type(e).__name__}:{e}",
                )
                return action

            self.active[player_id] = proc
            # Once the subprocess has spawned, the session-id is registered on
            # disk by claude even if we later kill it. Mark session_started now
            # so the NEXT call uses --resume (not --session-id, which would
            # fail with "session ID already in use").
            self.session_started[player_id] = True
            try:
                _, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=effective_timeout
                )
                if proc.returncode != 0:
                    logger.warning(
                        "agent %s exited %s: %s",
                        player_id,
                        proc.returncode,
                        (stderr or b"")[:500],
                    )
            except asyncio.TimeoutError:
                self._kill(proc)
                self._consume_bank(player_id, effective_timeout)
                action = _fold(
                    f"timeout (clock+bank exhausted at {effective_timeout:.0f}s)",
                    hand_id,
                )
                raw_timeout = af.read_text() if af.exists() else None
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="timeout",
                    action=action,
                    raw=raw_timeout,
                    write_success=bool(raw_timeout and raw_timeout.strip() not in ("", "{}")),
                )
                return action
            except Exception as e:
                self._kill(proc)
                self._consume_bank(player_id, time.time() - t_start)
                action = _fold(f"error:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="error",
                    action=action,
                    write_success=False,
                    parse_error=str(e),
                )
                return action
            finally:
                self.active.pop(player_id, None)
            elapsed = time.time() - t_start
            self._consume_bank(player_id, elapsed)

            if not af.exists():
                self._archive(workspace, player_id, hand_id, raw=None, parse_error="no_output")
                action = _fold("no_output", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="no_output",
                    action=action,
                    write_success=False,
                    return_code=proc.returncode,
                )
                return action
            raw = af.read_text()
            if raw.strip() in ("", "{}"):
                self._archive(workspace, player_id, hand_id, raw=raw, parse_error="no_output")
                action = _fold("no_output", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="no_output",
                    action=action,
                    raw=raw,
                    write_success=False,
                    return_code=proc.returncode,
                )
                return action
            try:
                action = _parse_action_lenient(raw, hand_id)
                self._archive(workspace, player_id, hand_id, raw=raw, parse_error=None)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="valid_action",
                    action=action,
                    raw=raw,
                    write_success=True,
                    return_code=proc.returncode,
                )
                return action
            except Exception as e:
                self._archive(workspace, player_id, hand_id, raw=raw, parse_error=str(e))
                action = _fold(f"bad_json:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="bad_json",
                    action=action,
                    raw=raw,
                    write_success=True,
                    parse_error=str(e),
                    return_code=proc.returncode,
                )
                return action

    def _build_env(self, player_id: str, model: str, workspace: Path) -> dict[str, str]:
        # Give each spawned claude its own HOME so it doesn't fight the parent
        # Claude Code session for ~/.claude/ lock files.
        agent_home = workspace / ".agent_home"
        agent_home.mkdir(parents=True, exist_ok=True)
        # Token gets forwarded as x-api-key, which the shim uses to look up
        # the configured model and forcibly override claude's model selection.
        token = self.player_tokens.get(player_id, "hab-internal")
        safe_env_keys = (
            "PATH",
            "TMPDIR",
            "TMP",
            "TEMP",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "REQUESTS_CA_BUNDLE",
        )
        env = {
            key: os.environ[key]
            for key in safe_env_keys
            if os.environ.get(key)
        }
        self._bootstrap_agent_home(
            agent_home=agent_home,
            workspace=workspace,
            token=token,
        )
        env.update({
            "HOME": str(agent_home),
            "ANTHROPIC_BASE_URL": self.shim_url,
            "ANTHROPIC_AUTH_TOKEN": token,
            "ANTHROPIC_API_KEY": token,
            "ANTHROPIC_MODEL": model,
            "PLAYER_ID": player_id,
        })
        return env

    def _bootstrap_agent_home(
        self,
        *,
        agent_home: Path,
        workspace: Path,
        token: str,
    ) -> None:
        """Prime an isolated Claude Code HOME without copying user history.

        Interactive Claude Code stores first-run/theme/API-key/trust state in
        HOME-level config files. Without these, a fresh benchmark HOME blocks
        on onboarding screens and times out before the model sees the poker
        prompt. Keep this file intentionally tiny: no host projects, sessions,
        todos, or transcript history are copied in.
        """
        claude_dir = agent_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)

        # Without a marketplace cache the claude CLI tries (and silently
        # fails) to git-clone anthropic/claude-plugins-official on every
        # startup, blowing the shot clock. Symlinking the host's read-only
        # cache lets claude skip the clone. Falls back to a no-op if the
        # host hasn't initialised one — e.g. CI runners.
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
        settings_path = claude_dir / "settings.json"
        if not settings_path.exists():
            settings_path.write_text(
                json.dumps(
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

        state_path = agent_home / ".claude.json"
        if state_path.exists():
            return
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
        project_paths = [str(workspace)]
        resolved_workspace = str(workspace.resolve())
        if resolved_workspace not in project_paths:
            project_paths.append(resolved_workspace)
        state_path.write_text(
            json.dumps(
                {
                    "numStartups": 0,
                    "customApiKeyResponses": {
                        "approved": [token],
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
                        path: trusted_project for path in project_paths
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    def pop_decision_record(self, player_id: str, hand_id: str) -> dict[str, Any] | None:
        return self._last_decisions.pop((player_id, hand_id), None)

    @staticmethod
    def _apply_openrouter_meta(record: dict[str, Any], meta: dict[str, Any]) -> None:
        record["api_runtime"] = "openrouter"
        record["mcp_tool_call_count"] = int(meta.get("mcp_tool_call_count") or 0)
        record["write_tool_call_count"] = int(meta.get("write_tool_call_count") or 0)
        record["permission_error_count"] = int(meta.get("permission_error_count") or 0)
        if meta.get("finish_reason"):
            record["finish_reason"] = meta["finish_reason"]
        if meta.get("tool_calls_used"):
            record["tool_calls_used"] = list(meta["tool_calls_used"])

    def _new_decision_record(
        self,
        *,
        player_id: str,
        model: str,
        hand_id: str,
        agent_kind: str,
        effective_timeout_sec: float,
        bank_before_sec: float | None = None,
    ) -> dict[str, Any]:
        bank_before = (
            bank_before_sec
            if bank_before_sec is not None
            else self.bank_remaining.get(player_id, self._starting_bank)
        )
        return {
            "schema_version": DECISION_SCHEMA_VERSION,
            "player_id": player_id,
            "model": model,
            "hand_id": hand_id,
            "agent_kind": agent_kind,
            "started_at": self._utc_now(),
            "decision_clock_sec": self.decision_clock,
            "effective_timeout_sec": effective_timeout_sec,
            "bank_before_sec": bank_before,
            "unsafe_permissions": self.unsafe_skip_permissions,
        }

    def _finish_decision_record(
        self,
        *,
        workspace: Path,
        record: dict[str, Any],
        t_start: float,
        outcome: str,
        action: Action,
        write_success: bool,
        raw: str | None = None,
        parse_error: str | None = None,
        return_code: int | None = None,
    ) -> None:
        elapsed = max(0.0, time.time() - t_start)
        effective_timeout = float(record.get("effective_timeout_sec") or 0.0)
        player_id = str(record["player_id"])
        hand_id = str(record["hand_id"])
        log_stats = self._decision_log_stats(
            workspace=workspace,
            session_id=record.get("agent_session_id"),
        )
        record.update({
            "ended_at": self._utc_now(),
            "elapsed_sec": round(elapsed, 4),
            "timeout_fraction": (
                round(min(1.0, elapsed / effective_timeout), 4)
                if effective_timeout > 0
                else None
            ),
            "bank_after_sec": self.bank_remaining.get(player_id, self._starting_bank),
            "outcome": outcome,
            "action": action.action,
            "amount": action.amount,
            "reason": action.reason,
            "tool_calls_used": list(action.tool_calls_used or []),
            "write_success": write_success,
            "raw_action_bytes": len(raw.encode("utf-8")) if raw is not None else 0,
            "return_code": return_code,
            **log_stats,
        })
        if parse_error:
            record["parse_error"] = parse_error
        self._last_decisions[(player_id, hand_id)] = record

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _persistent_claude_cmd(
        self,
        *,
        workspace: Path,
        player_id: str,
        session_id: str,
    ) -> list[str]:
        cmd = [
            self.claude_binary,
            "--bare",
            "--effort", self.claude_effort,
            "--append-system-prompt",
            (workspace / "CLAUDE.md").read_text() if (workspace / "CLAUDE.md").exists() else "",
            "--session-id",
            session_id,
            "--name",
            f"HAB {player_id}",
        ]
        if self.unsafe_skip_permissions:
            cmd.append("--dangerously-skip-permissions")
        else:
            cmd += [
                "--permission-mode", "acceptEdits",
                "--allowedTools", ",".join(_ALLOWED_REAL_AGENT_TOOLS),
                "--disallowedTools", "Bash",
            ]
        mcp_cfg = workspace / ".claude" / "mcp_servers.json"
        if mcp_cfg.exists():
            cmd += ["--mcp-config", str(mcp_cfg), "--strict-mcp-config"]
        return cmd

    def _persistent_turn_prompt(
        self,
        *,
        player_id: str,
        hand_id: str,
        effective_timeout: float,
        first_turn: bool,
    ) -> str:
        if first_turn:
            prefix = (
                f"You are {player_id} in a HoldemAgentBench match. This Claude "
                "Code CLI process will stay alive for the entire session. "
                "Read CLAUDE.md and the skills as needed, then handle this turn."
            )
        else:
            prefix = f"Next poker decision for {player_id}."
        return (
            f"{prefix}\n\n"
            f"hand_id={hand_id}. Re-read game_view/current_state.json and "
            "game_view/hole_cards.json, decide, write actions/action.json with "
            "the Write or Edit tool, then stop and wait for my next message. "
            "Do not use Bash, shell commands, or nested claude processes; call "
            "the poker MCP tools directly when needed. actions/action.json "
            "already contains `{}`; if using Edit, replace old_string `{}`.\n\n"
            f"Shot clock for this decision: {effective_timeout:.0f}s total. "
            "If you do not write a valid action file in time, the engine will "
            "force-fold you."
        )

    def _decision_log_stats(
        self,
        *,
        workspace: Path,
        session_id: Any,
    ) -> dict[str, int]:
        empty = {
            "permission_error_count": 0,
            "mcp_tool_call_count": 0,
            "write_tool_call_count": 0,
        }
        if not session_id:
            return empty
        root = workspace / ".agent_home" / ".claude" / "projects"
        if not root.exists():
            return empty
        try:
            matches = list(root.rglob(f"{session_id}.jsonl"))
        except Exception:
            return empty
        if not matches:
            return empty
        path = max(matches, key=lambda p: p.stat().st_mtime)
        key = str(path)
        try:
            size = path.stat().st_size
            offset = self._session_log_offsets.get(key, 0)
            if offset > size:
                offset = 0
            with path.open("rb") as f:
                f.seek(offset)
                data = f.read()
            self._session_log_offsets[key] = size
        except Exception:
            return empty

        text = data.decode("utf-8", errors="ignore")
        lower = text.lower()
        permission_patterns = (
            "requested permissions",
            "requires approval",
            "permission denied",
            "not allowed",
            "was blocked",
        )
        permission_errors = sum(lower.count(p) for p in permission_patterns)
        write_tool_calls = 0
        for tool_name in ("Write", "Edit"):
            write_tool_calls += text.count(f'"name":"{tool_name}"')
            write_tool_calls += text.count(f'"name": "{tool_name}"')
        return {
            "permission_error_count": permission_errors,
            "mcp_tool_call_count": text.count("mcp__hab-poker-toolkit__"),
            "write_tool_call_count": write_tool_calls,
        }

    def _kill(self, proc: asyncio.subprocess.Process) -> None:
        try:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

    def _consume_bank(self, player_id: str, elapsed: float) -> None:
        over = max(0.0, elapsed - self.decision_clock)
        if over <= 0:
            return
        current = self.bank_remaining.get(player_id, self._starting_bank)
        self.bank_remaining[player_id] = max(0.0, current - over)

    @staticmethod
    def _archive(
        workspace: Path,
        player_id: str,
        hand_id: str,
        raw: str | None,
        parse_error: str | None,
    ) -> None:
        """Persist the raw action.json contents per-decision, since action.json is
        overwritten on the next decision. Lets us debug LLM output drift."""
        try:
            logs = workspace / "logs"
            logs.mkdir(parents=True, exist_ok=True)
            archive = logs / "decisions.jsonl"
            entry = {
                "hand_id": hand_id,
                "player_id": player_id,
                "raw": raw,
                "parse_error": parse_error,
            }
            with archive.open("a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # never let archival kill a session

    async def shutdown(self) -> None:
        for proc in list(self.active.values()):
            self._kill(proc)
        self.active.clear()
        for agent in list(self._openrouter_agents.values()):
            await agent.aclose()
        self._openrouter_agents.clear()
        for agent in list(self._persistent_claude_agents.values()):
            await agent.close()
        self._persistent_claude_agents.clear()
