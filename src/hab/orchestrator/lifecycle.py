"""HABSession: top-level coordinator.

Starts shim → builds workspaces → drives engine event loop, asking the agent pool
for each action and forwarding it back to the engine.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from hab.engine.actions import validate_action
from hab.engine.game_master import GameMaster, GameMasterConfig
from hab.engine.recorder import HandRecorder
from hab.engine.state import HandResult
from hab.orchestrator.agent_pool import AgentPool
from hab.orchestrator.decision_metrics import summarize_decisions
from hab.orchestrator.live_view import LiveDisplay
from hab.orchestrator.progress import ProgressDisplay
from hab.orchestrator.workspace_manager import WorkspaceManager
from hab.shim.server import ShimServer


@dataclass
class SessionConfig:
    players: dict[str, str]               # player_id -> model
    hands_target: int = 100
    small_blind: float = 1.0
    big_blind: float = 2.0
    starting_stack: float = 200.0
    output_dir: Path = field(default_factory=lambda: Path.home() / "hab-sessions")
    max_concurrent_agents: int = 4
    decision_timeout_sec: float = 300.0
    seed: int | None = 42
    openrouter_key: str | None = None
    anthropic_key: str | None = None
    live: bool = True
    # Shot-clock + time bank (Triton-style, calibrated for claude CLI overhead).
    # Real human poker is 30s base + N tokens, but claude CLI itself burns ~60s
    # before the model even starts. We bump base to 90s and shrink the bank.
    decision_clock_sec: float = 90.0
    time_bank_tokens: int = 3
    time_bank_token_sec: float = 60.0
    unsafe_skip_permissions: bool = False
    duplicate_templates: bool = False
    agent_runtime: str = "claude-code-persistent"
    claude_effort: str = "low"


class HABSession:
    def __init__(self, config: SessionConfig):
        self.config = config
        self.session_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.session_dir = config.output_dir / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.shim: ShimServer | None = None
        self.pool: AgentPool | None = None
        self.workspaces: WorkspaceManager | None = None
        self.recorder: HandRecorder | None = None
        self.gm: GameMaster | None = None
        self.progress: ProgressDisplay | None = None
        self.decision_records: list[dict] = []

    async def run(self) -> dict:
        try:
            await self._startup()
            return await self._main_loop()
        finally:
            await self._cleanup()

    async def _startup(self) -> None:
        needs_shim = (
            self.config.agent_runtime in {"claude-code", "claude-code-persistent"}
            and any(not m.startswith("mock://") for m in self.config.players.values())
        )
        player_tokens: dict[str, str] = {}
        if needs_shim:
            self.shim = ShimServer(
                openrouter_key=self.config.openrouter_key,
                anthropic_key=self.config.anthropic_key,
            )
            for pid, model in self.config.players.items():
                if not model.startswith("mock://"):
                    player_tokens[pid] = self.shim.register_player(pid, model)
            await self.shim.start()
            shim_url = self.shim.base_url
        else:
            shim_url = "http://localhost:1"

        self.workspaces = WorkspaceManager(self.session_dir)
        self.recorder = HandRecorder(self.session_dir)
        for pid, model in self.config.players.items():
            self.workspaces.create(pid, model)

        self.pool = AgentPool(
            shim_url=shim_url,
            max_concurrent=self.config.max_concurrent_agents,
            decision_timeout=self.config.decision_timeout_sec,
            player_tokens=player_tokens,
            players=list(self.config.players.keys()),
            decision_clock_sec=self.config.decision_clock_sec,
            time_bank_tokens=self.config.time_bank_tokens,
            time_bank_token_sec=self.config.time_bank_token_sec,
            unsafe_skip_permissions=self.config.unsafe_skip_permissions,
            agent_runtime=self.config.agent_runtime,
            openrouter_key=self.config.openrouter_key,
            claude_effort=self.config.claude_effort,
        )

        gm_cfg = GameMasterConfig(
            players=list(self.config.players.keys()),
            small_blind=self.config.small_blind,
            big_blind=self.config.big_blind,
            starting_stack=self.config.starting_stack,
            hands_target=self.config.hands_target,
            seed=self.config.seed,
            decision_timeout_sec=self.config.decision_timeout_sec,
            duplicate_templates=self.config.duplicate_templates,
        )
        self.gm = GameMaster(gm_cfg)
        self.live: LiveDisplay | None = None
        if self.config.live:
            self.live = LiveDisplay(
                players=list(self.config.players.keys()),
                hands_target=self.config.hands_target,
            )
            self.progress = None
        else:
            self.progress = ProgressDisplay(
                total_hands=self.config.hands_target,
                players=list(self.config.players.keys()),
            )

    async def _main_loop(self) -> dict:
        assert self.gm and self.pool and self.workspaces and self.recorder
        result_payload: dict = {}
        async for event in self.gm.events():
            if event.type == "hand_start" and self.live:
                self.live.hand_start(event)
            elif event.type == "action_needed":
                ws = self.workspaces.workspaces_dir / event.player_id
                self.recorder.write_game_view(ws, event.game_view, event.hole_cards)
                self.recorder.reset_action_dir(ws)
                if self.live:
                    self.live.action_needed(event)
                action = await self.pool.request_action(
                    player_id=event.player_id,
                    model=self.config.players[event.player_id],
                    workspace=ws,
                    hand_id=event.hand_id,
                )
                decision_record = self.pool.pop_decision_record(
                    event.player_id,
                    event.hand_id,
                )
                if decision_record is not None:
                    validation_error = validate_action(action, event.legal_actions)
                    decision_record["engine_valid"] = validation_error is None
                    decision_record["engine_validation_error"] = validation_error
                    if validation_error and decision_record.get("outcome") == "valid_action":
                        decision_record["outcome"] = "invalid_action"
                    self.decision_records.append(decision_record)
                    self.recorder.write_decision_record(decision_record)
                if self.live:
                    bank = self.pool.bank_remaining.get(event.player_id) if self.pool else None
                    self.live.action_taken(event, action, bank_remaining=bank)
                await self.gm.submit_action(event.player_id, action)
            elif event.type == "hand_complete":
                hr = HandResult.model_validate(event.payload)
                self.recorder.write_hand_result(hr)
                stacks = {p: self.gm.stacks[p] for p in self.config.players}
                if self.live:
                    self.live.hand_complete(event)
                elif self.progress:
                    self.progress.hand_complete(event.hand_id, stacks)
            elif event.type == "session_complete":
                result_payload = event.payload
                if self.live:
                    self.live.session_done()
                elif self.progress:
                    self.progress.session_done()

        all_mock = bool(self.config.players) and all(
            m.startswith("mock://") for m in self.config.players.values()
        )
        effective_runtime = "mock" if all_mock else self.config.agent_runtime

        summary_path = self.session_dir / "session_summary.json"
        summary_path.write_text(json.dumps({
            "session_id": self.session_id,
            "ended_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "players": self.config.players,
            "hands_target": self.config.hands_target,
            "small_blind": self.config.small_blind,
            "big_blind": self.config.big_blind,
            "starting_stack": self.config.starting_stack,
            "duplicate_templates_enabled": self.config.duplicate_templates,
            "agent_runtime": effective_runtime,
            "duplicate_mode": (
                "template_rotation" if self.config.duplicate_templates else None
            ),
            "chip_accounting": (
                "duplicate_rebuy_net"
                if self.config.duplicate_templates
                else "continuous_stack"
            ),
            "agent_security": {
                "environment": "n/a" if all_mock else "allowlist",
                "unsafe_permissions": self.config.unsafe_skip_permissions,
                "permission_mode": (
                    "n/a"
                    if all_mock
                    else "in_process_tool_executor"
                    if self.config.agent_runtime == "openrouter"
                    else "bypassPermissions"
                    if self.config.unsafe_skip_permissions
                    else "acceptEdits_with_tool_allowlist"
                ),
                "filesystem_sandbox": "not_enforced",
            },
            **{k: v for k, v in result_payload.items() if k != "history"},
            "hands_recorded": len(result_payload.get("history", [])),
            "decisions_recorded": len(self.decision_records),
            "decision_summary": summarize_decisions(self.decision_records),
        }, indent=2, default=str))
        return result_payload

    async def _cleanup(self) -> None:
        if self.pool:
            await self.pool.shutdown()
        if self.shim:
            await self.shim.stop()
