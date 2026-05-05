# Fix `claude-code-persistent` Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the `claude-code-persistent` runtime end-to-end functional: prompts submit to claude, claude calls MCP tools, `actions/action.json` receives non-fold decisions, hands complete by skill not by timeout.

**Architecture:** Three layered fixes built incrementally with a fast feedback loop. (1) Replace the broken `\r` PTY submit with bracketed-paste — wraps the prompt so claude CLI v2.1.x recognises "input complete" — with LF and Claude Agent SDK as fallbacks. (2) Stop killing the persistent process on a single timeout: introduce a soft `interrupt()` that sends Ctrl+C plus a status note, so the conversation survives. (3) Build a 30-second smoke script that targets the real `claude` binary on a single non-poker prompt, so each fix attempt costs cents and seconds rather than dollars and hours, and back it with a gated pytest e2e for regression.

**Tech Stack:** Python 3.11, asyncio, `pty`/`fcntl`, pytest, the existing `claude` CLI v2.1.126.

**Pre-flight:** these tasks assume a working venv (`source .venv/bin/activate`), the `claude` binary on PATH, and `OPENROUTER_API_KEY` exported in the shell — referred to below as `$KEY`. If you don't have a key, [generate one](https://openrouter.ai/keys); a few US$ of credit covers the whole plan.

---

## File Structure

**Modified:**
- `src/hab/orchestrator/claude_persistent.py` — bracketed-paste in `_send` (Task 2), `interrupt()` method (Task 5)
- `src/hab/orchestrator/agent_pool.py` (lines ~411-447) — replace pop+kill on timeout with `interrupt()` (Task 5)
- `tests/unit/test_claude_persistent.py` — pin the new submit byte sequence + interrupt behaviour (Tasks 4, 5)
- `README.md` — drop any "experimental" caveat once validated (Task 7)

**Created:**
- `scripts/smoke_persistent.py` — ~30s diagnostic that drives one real claude turn (Task 1)
- `tests/e2e/test_persistent_runtime.py` — gated end-to-end test on `HAB_E2E_CLAUDE=1` (Task 6)

**Untouched but referenced:**
- `src/hab/orchestrator/agent_pool.py:923-952` (`_persistent_claude_cmd`) — already passes `--bare`, no change
- `src/hab/cli/run.py` — `--decision-timeout-sec` flag already added in commit 532ae08

---

## Task 1: Diagnostic smoke script

A 30-second test harness for the PTY submit path: spawn one real claude, ask it to write `marker.txt`, watch whether the file appears. Decouples the Bug #3 fix iteration from a 50-hand poker match. Each iteration costs cents and seconds.

**Files:**
- Create: `scripts/smoke_persistent.py`

- [ ] **Step 1: Create the smoke script**

```python
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
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from hab.orchestrator.claude_persistent import (
    PersistentClaudeProcess,
    PersistentClaudeTimeout,
)
from hab.shim.server import ShimServer


async def main() -> int:
    or_key = os.environ.get("OPENROUTER_API_KEY")
    if not or_key:
        print("OPENROUTER_API_KEY required", file=sys.stderr)
        return 2

    workspace = Path("/tmp/hab-smoke-persistent")
    if workspace.exists():
        import shutil
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
    token = shim.register_player("smoke", "anthropic/claude-haiku-4-5")
    await shim.start()
    try:
        env = os.environ.copy()
        env["ANTHROPIC_BASE_URL"] = shim.base_url
        env["ANTHROPIC_API_KEY"] = token
        env["HOME"] = str(agent_home)

        cmd = [
            "claude", "--bare", "--effort", "low",
            "--append-system-prompt", (workspace / "CLAUDE.md").read_text(),
            "--permission-mode", "acceptEdits",
            "--allowedTools", "Write,Edit,Read",
            "--disallowedTools", "Bash",
        ]
        agent = PersistentClaudeProcess(
            player_id="smoke",
            workspace=workspace,
            cmd=cmd,
            env=env,
            log_path=workspace / "smoke.log",
        )
        await agent.ensure_started()

        prompt = (
            f"Write the file marker.txt in this directory with the literal "
            f"content 'hello-{int(time.time())}', using the Write tool. "
            f"Then stop and wait for my next message."
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
        except PersistentClaudeTimeout:
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
```

- [ ] **Step 2: Run the smoke script — expect baseline failure**

Run: `chmod +x scripts/smoke_persistent.py && OPENROUTER_API_KEY=$KEY python scripts/smoke_persistent.py`

Expected: FAIL — `marker file not written` after 180s. Inspect `/tmp/hab-smoke-persistent/smoke.log`; the prompt should be visible in claude's input area but no submission, no LLM call, no Write tool use. This reproduces Bug #3.

- [ ] **Step 3: Commit**

```bash
git add scripts/smoke_persistent.py
git commit -m "Add smoke script for persistent runtime PTY debugging"
```

---

## Task 2: Bracketed-paste submission (Bug #3 fix attempt 1)

Most likely root cause: the chunked `os.write` burst plus a bare trailing `\r` looks indistinguishable from "still typing" to claude CLI v2.1.x. Wrapping the prompt in `\x1b[200~ ... \x1b[201~` is the standard terminal "this is a paste, ends here" signal — after the `\x1b[201~` claude knows the input is complete and treats `\r` as a real submit.

**Files:**
- Modify: `src/hab/orchestrator/claude_persistent.py:106-116` (the `_send` method)

- [ ] **Step 1: Replace `_send` body with bracketed paste**

In `src/hab/orchestrator/claude_persistent.py`, replace the entire `_send` method:

```python
    def _send(self, prompt: str) -> None:
        if self._master_fd is None:
            raise PersistentClaudeError("persistent Claude process not started")
        payload = " ".join(prompt.split()).encode("utf-8")
        # Wrap in bracketed-paste markers so the TUI treats this as a
        # single atomic paste, then submit with \r. claude CLI v2.1.x
        # ignores a bare \r following a chunked write because it cannot
        # distinguish "still typing" from "done". Bracketed paste is the
        # explicit "input complete" signal.
        os.write(self._master_fd, b"\x1b[200~")
        for i in range(0, len(payload), 4096):
            os.write(self._master_fd, payload[i : i + 4096])
        os.write(self._master_fd, b"\x1b[201~")
        time.sleep(0.4)
        os.write(self._master_fd, b"\r")
```

- [ ] **Step 2: Re-run the smoke script**

Run: `OPENROUTER_API_KEY=$KEY python scripts/smoke_persistent.py`

Expected outcomes:
- **PASS** — `marker.txt` written within ~30-60s. Bug #3 fixed; proceed to Task 4.
- **FAIL but log shows "Bash"/"Write" tool invocations starting** — bracketed paste partially worked but timing or tool permissions are off. Inspect `/tmp/hab-smoke-persistent/smoke.log` for new tool errors and adjust before proceeding.
- **FAIL identical to baseline** (no log activity past the prompt display) — bracketed paste didn't unblock the submit. Revert this commit and proceed to Task 3.

- [ ] **Step 3: Commit (only on PASS)**

```bash
git add src/hab/orchestrator/claude_persistent.py
git commit -m "Submit prompts via bracketed paste — fixes claude CLI 2.1.x submit hang (#3)"
```

---

## Task 3: Fallback fix paths (only if Task 2 fails)

Skip this task entirely if Task 2 succeeded.

**Files:**
- Modify: `src/hab/orchestrator/claude_persistent.py` (`_send` method)

- [ ] **Step 1a: Try LF / CRLF submit**

Revert the bracketed-paste change. In `_send`, replace the trailing `\r\r` block with:

```python
        time.sleep(0.4)
        os.write(self._master_fd, b"\n")
        time.sleep(0.6)
        os.write(self._master_fd, b"\r\n")
```

Run smoke. If PASS → commit and skip to Task 4 (update Task 4's expected byte assertions accordingly).

- [ ] **Step 1b: Try keypad Enter escape sequence**

If LF didn't work either, try the application-mode keypad Enter sequence many TUIs use:

```python
        time.sleep(0.4)
        os.write(self._master_fd, b"\x1bOM")  # SS3 + M = Enter in keypad app mode
        time.sleep(0.4)
        os.write(self._master_fd, b"\r")
```

Run smoke. If PASS → commit.

- [ ] **Step 1c: Switch to Claude Agent SDK / headless protocol**

If keystroke variants all fail, the TUI input path is the wrong abstraction. Stop iterating on `_send` and write a sub-spec at `docs/plans/2026-05-05-persistent-via-sdk.md` covering:
1. SDK or non-TUI claude entry point (e.g. `claude` JSON-RPC mode if it exists, the `@anthropic-ai/claude-agent-sdk` npm package, or some `--input-fd` protocol).
2. Mapping from `PersistentClaudeProcess.request_action` to the SDK's send-receive pair.
3. How to keep `--mcp-config` and `--append-system-prompt` semantics.
4. Error/timeout handling shape.

That sub-spec gets executed via this same writing-plans skill. Do not start coding without it — the SDK switch is large enough to deserve its own brainstorm.

- [ ] **Step 2: Commit the working variant**

```bash
git add src/hab/orchestrator/claude_persistent.py
git commit -m "<describe which submit variant unblocked claude>"
```

---

## Task 4: Pin the submit byte sequence with a unit test

Lock in whichever sequence worked in Task 2/3 so future PTY tinkering can't silently regress.

**Files:**
- Modify: `tests/unit/test_claude_persistent.py`

- [ ] **Step 1: Add the byte-sequence test (assumes bracketed-paste won)**

Append to `tests/unit/test_claude_persistent.py`:

```python
def test_send_uses_bracketed_paste(monkeypatch):
    """_send must wrap the prompt in bracketed-paste markers and trail with \\r.
    
    If this test fails after a refactor, run scripts/smoke_persistent.py to
    check whether the new sequence still actually submits to claude before
    updating these assertions.
    """
    from hab.orchestrator.claude_persistent import PersistentClaudeProcess

    writes: list[bytes] = []

    def fake_write(fd, data):
        writes.append(data)
        return len(data)

    monkeypatch.setattr("hab.orchestrator.claude_persistent.os.write", fake_write)
    monkeypatch.setattr("hab.orchestrator.claude_persistent.time.sleep", lambda *_: None)

    p = PersistentClaudeProcess.__new__(PersistentClaudeProcess)
    p._master_fd = 3
    p._send("hello world")

    blob = b"".join(writes)
    assert blob.startswith(b"\x1b[200~"), f"missing paste-start: {blob[:20]!r}"
    assert b"\x1b[201~" in blob, "missing paste-end"
    assert blob.rstrip().endswith(b"\r"), f"missing trailing \\r: {blob[-5:]!r}"
    assert b"hello world" in blob
```

If a different variant won in Task 3, replace the assertions with that variant's signature (e.g. for LF: `assert b"\n" in blob and blob.endswith(b"\r\n")`).

- [ ] **Step 2: Run the test, expect PASS**

Run: `pytest tests/unit/test_claude_persistent.py::test_send_uses_bracketed_paste -v`
Expected: PASS

- [ ] **Step 3: Run full unit suite, no regressions**

Run: `pytest tests/unit/ -q`
Expected: at least 129 tests, all green (was 128 before; one new).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/test_claude_persistent.py
git commit -m "Pin _send byte sequence so PTY refactors can't silently regress submit"
```

---

## Task 5: Soft-interrupt instead of kill+respawn (Bug #1 fix)

After Bug #3 is resolved, timeouts should be rare — but they'll still happen (rate limits, slow models, mid-decision crashes). Today, any single timeout pops the persistent agent and forces a cold respawn for the next hand, which is why one bad turn used to ruin a whole 50-hand match. Instead, send Ctrl+C to abort claude's current task, follow with a status note, and reuse the same process for the next decision.

**Files:**
- Modify: `src/hab/orchestrator/claude_persistent.py` — add `interrupt()` method
- Modify: `src/hab/orchestrator/agent_pool.py` (lines ~411-447) — call `interrupt()` from the timeout/bad_json branches; keep kill+pop only for `no_output` (process is dead/wedged)
- Modify: `tests/unit/test_claude_persistent.py` — test the interrupt method

- [ ] **Step 1: Write the failing interrupt test**

Append to `tests/unit/test_claude_persistent.py`:

```python
@pytest.mark.asyncio
async def test_interrupt_keeps_process_alive(monkeypatch):
    """interrupt() must NOT terminate the underlying process — it sends
    Ctrl+C plus a context-resetting note and returns. The orchestrator
    can then reuse the same process for the next decision."""
    from hab.orchestrator.claude_persistent import PersistentClaudeProcess

    p = PersistentClaudeProcess.__new__(PersistentClaudeProcess)
    p._master_fd = 3
    p.proc = type("StubProc", (), {"returncode": None, "pid": 99})()

    sends: list[bytes] = []
    monkeypatch.setattr(
        "hab.orchestrator.claude_persistent.os.write",
        lambda fd, data: sends.append(data) or len(data),
    )

    async def noop_sleep(*_args, **_kw):
        return None

    monkeypatch.setattr("hab.orchestrator.claude_persistent.asyncio.sleep", noop_sleep)

    await p.interrupt(reason="test timeout")

    assert p.proc.returncode is None, "interrupt() must not terminate the process"
    blob = b"".join(sends)
    assert b"\x03" in blob, "interrupt() must send ETX (Ctrl+C)"
    assert b"force-folded" in blob, "interrupt() must include the reason note"
```

- [ ] **Step 2: Run the test, expect FAIL with AttributeError**

Run: `pytest tests/unit/test_claude_persistent.py::test_interrupt_keeps_process_alive -v`
Expected: FAIL — `AttributeError: 'PersistentClaudeProcess' object has no attribute 'interrupt'`.

- [ ] **Step 3: Implement `interrupt()` in `PersistentClaudeProcess`**

In `src/hab/orchestrator/claude_persistent.py`, add this method to `PersistentClaudeProcess`, immediately after the existing `close` method:

```python
    async def interrupt(self, *, reason: str) -> None:
        """Abort the current claude task without killing the process.

        The orchestrator calls this when a decision times out or returns
        bad JSON. Goal: keep the persistent claude process alive so the
        next decision reuses its loaded skills and conversation context,
        avoiding the ~30-60s cold-start cost of a respawn.

        Sequence: ETX (Ctrl+C) cancels claude's in-flight task, then a
        bracketed-paste status note lets claude know its previous turn
        was force-folded so any half-formed reasoning can be dropped.
        """
        if (
            self._master_fd is None
            or self.proc is None
            or self.proc.returncode is not None
        ):
            return
        os.write(self._master_fd, b"\x03")
        await asyncio.sleep(0.5)
        msg = (
            f"[orchestrator] previous decision force-folded ({reason}). "
            f"The next poker decision will arrive shortly; ignore any "
            f"unfinished work from the previous turn."
        )
        os.write(self._master_fd, b"\x1b[200~" + msg.encode("utf-8") + b"\x1b[201~")
        await asyncio.sleep(0.3)
        os.write(self._master_fd, b"\r")
```

- [ ] **Step 4: Run interrupt test, expect PASS**

Run: `pytest tests/unit/test_claude_persistent.py::test_interrupt_keeps_process_alive -v`
Expected: PASS

- [ ] **Step 5: Wire `interrupt()` into the agent_pool timeout branches**

In `src/hab/orchestrator/agent_pool.py`, locate the three `except` blocks inside `_persistent_claude_action` (the `PersistentClaudeTimeout`, `PersistentClaudeNoOutput`, `PersistentClaudeBadJson` handlers — currently around lines 411-463). Replace **only the timeout and bad_json branches** with calls to `interrupt()`. Keep `no_output` as kill+pop because that error means the process exited or is unrecoverably wedged.

The new bodies (the `try:` and `valid_action` branches above are unchanged):

```python
            except PersistentClaudeTimeout as e:
                self._consume_bank(player_id, effective_timeout)
                await agent.interrupt(reason="shot clock + bank exhausted")
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
                    write_success=bool(
                        raw_timeout and raw_timeout.strip() not in ("", "{}")
                    ),
                    parse_error=str(e),
                )
                record["api_runtime"] = "claude-code-persistent"
                return action
            except PersistentClaudeNoOutput as e:
                # NoOutput = process exited or wedged. Interrupt won't help.
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
                await agent.interrupt(reason=f"bad action json: {e}")
                raw_bad = af.read_text() if af.exists() else None
                action = _fold(f"bad_json:{e}", hand_id)
                self._finish_decision_record(
                    workspace=workspace,
                    record=record,
                    t_start=t_start,
                    outcome="bad_json",
                    action=action,
                    raw=raw_bad,
                    write_success=bool(
                        raw_bad and raw_bad.strip() not in ("", "{}")
                    ),
                    parse_error=str(e),
                )
                record["api_runtime"] = "claude-code-persistent"
                return action
```

Diff vs the current code:
- Timeout branch: `agent.close(kill=True)` + `pop(...)` → `agent.interrupt(...)`
- BadJson branch: previously fell through with no cleanup → now calls `interrupt(...)`
- NoOutput branch: unchanged (still kill+pop)

- [ ] **Step 6: Re-run the smoke script to verify the patched runtime still works**

Run: `OPENROUTER_API_KEY=$KEY python scripts/smoke_persistent.py`
Expected: PASS (just confirming Task 5 didn't regress Task 2's fix).

- [ ] **Step 7: Run full test suite**

Run: `pytest tests/ -q`
Expected: at least 130 tests, all green (was 128 before; +1 byte-seq test, +1 interrupt test).

- [ ] **Step 8: Commit**

```bash
git add src/hab/orchestrator/claude_persistent.py src/hab/orchestrator/agent_pool.py tests/unit/test_claude_persistent.py
git commit -m "Soft-interrupt persistent claude on timeout/bad_json instead of kill+respawn (#1)"
```

---

## Task 6: Gated end-to-end pytest

Smoke script tests the submit primitive. This test exercises the full lifecycle: 3-hand HU match against a real claude opponent vs a mock, with assertions on harness telemetry. Slow (~3-5min) and costs ~$0.10 so it's gated on `HAB_E2E_CLAUDE=1` and skipped in normal CI.

**Files:**
- Create: `tests/e2e/test_persistent_runtime.py`

- [ ] **Step 1: Write the e2e test**

```python
"""End-to-end test for the claude-code-persistent runtime.

Gated on HAB_E2E_CLAUDE=1 because it requires:
  - the `claude` CLI on PATH
  - OPENROUTER_API_KEY exported
  - ~5 minutes wall time

    HAB_E2E_CLAUDE=1 OPENROUTER_API_KEY=sk-or-... \\
        pytest tests/e2e/test_persistent_runtime.py -v -s
"""
from __future__ import annotations
import json
import os
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("HAB_E2E_CLAUDE") != "1"
    or not os.environ.get("OPENROUTER_API_KEY"),
    reason="HAB_E2E_CLAUDE=1 + OPENROUTER_API_KEY required",
)


@pytest.mark.asyncio
async def test_persistent_runtime_completes_three_hands(tmp_path: Path):
    if shutil.which("claude") is None:
        pytest.skip("claude CLI not found on PATH")

    from hab.orchestrator.lifecycle import HABSession, SessionConfig

    cfg = SessionConfig(
        players={
            "player_a": "anthropic/claude-haiku-4-5",
            "player_b": "mock://always-fold",
        },
        hands_target=3,
        small_blind=1.0,
        big_blind=2.0,
        starting_stack=200.0,
        output_dir=tmp_path,
        seed=42,
        decision_clock_sec=120.0,
        time_bank_tokens=3,
        time_bank_token_sec=60.0,
        decision_timeout_sec=600.0,
        agent_runtime="claude-code-persistent",
        openrouter_key=os.environ["OPENROUTER_API_KEY"],
        live=False,
    )
    session = HABSession(cfg)
    result = await session.run()

    assert result["hands_played"] == 3, f"expected 3 hands, got {result['hands_played']}"

    decisions = [
        json.loads(line)
        for line in (
            session.session_dir / "decision_log.jsonl"
        ).read_text().splitlines()
    ]
    real = [d for d in decisions if d["model"] == "anthropic/claude-haiku-4-5"]
    assert real, "claude-haiku played zero decisions"

    # Anti-Bug-#3 assertion: claude must produce real decisions, not just
    # timeout-folds. At least one valid action AND at least one MCP tool
    # call across the match.
    valid = [d for d in real if d["outcome"] == "valid_action"]
    assert valid, (
        f"all of claude's decisions timed out or errored: "
        f"{[d['outcome'] for d in real]}"
    )
    total_tools = sum(d.get("mcp_tool_call_count", 0) for d in real)
    assert total_tools >= 1, (
        f"claude never called an MCP tool — toolkit not exposed correctly. "
        f"Decisions: {[(d['outcome'], d.get('mcp_tool_call_count', 0)) for d in real]}"
    )
```

- [ ] **Step 2: Confirm e2e test stays gated off in normal runs**

Run: `pytest tests/ -q`
Expected: 130 tests, all pass; the e2e test is skipped (no `HAB_E2E_CLAUDE=1`).

- [ ] **Step 3: Run the e2e test locally with the real claude binary**

Run: `HAB_E2E_CLAUDE=1 OPENROUTER_API_KEY=$KEY pytest tests/e2e/test_persistent_runtime.py -v -s`
Expected: PASS within ~5 minutes.
- If it fails on `valid` assertion → claude is still timing out. Re-run smoke (Task 1 step 2) to confirm submit works in isolation; if smoke passes but e2e fails, the issue is in lifecycle/agent_pool, not `_send`. Investigate before continuing.
- If it fails on `total_tools >= 1` → claude submits and acts but isn't reaching the MCP toolkit. Check `--mcp-config` and `--strict-mcp-config` flags in `_persistent_claude_cmd`, and confirm `workspace/.claude/mcp_servers.json` exists for the test workspace.

- [ ] **Step 4: Commit**

```bash
git add tests/e2e/test_persistent_runtime.py
git commit -m "Add gated e2e test for claude-code-persistent runtime"
```

---

## Task 7: Validation run + close issues

Confirm the full pipeline works on a real benchmark match, document the win, close the open issues, push.

- [ ] **Step 1: 10-hand HU validation run with two real models**

Run:
```bash
source .venv/bin/activate
OPENROUTER_API_KEY=$KEY hab run quickstart \
  --models anthropic/claude-haiku-4-5,openai/gpt-5-mini \
  --hands 10 \
  --decision-timeout-sec 600 \
  --output /tmp/hab-validate
```
Expected: completes in ~10–15 min. Cost: ~$0.30–0.80.

- [ ] **Step 2: Inspect telemetry**

```bash
SID=$(ls /tmp/hab-validate/)
python3 - <<EOF
import json
from collections import Counter
d = [json.loads(l) for l in open(f'/tmp/hab-validate/$SID/decision_log.jsonl')]
print(f'total decisions: {len(d)}')
for m in {r["model"] for r in d}:
    rows = [r for r in d if r['model'] == m]
    outcomes = Counter(r['outcome'] for r in rows)
    tools = sum(r.get('mcp_tool_call_count', 0) for r in rows)
    valid_rate = outcomes.get('valid_action', 0) / len(rows)
    print(f'  {m}: {dict(outcomes)} | tools={tools} | valid_rate={valid_rate:.0%}')
EOF
```
Expected per non-mock model: `valid_rate >= 70%` and `tools >= 1`. If those hold, plan C is validated end-to-end.

- [ ] **Step 3: Export and commit the run as the first real official run**

```bash
hab export-run /tmp/hab-validate/$SID --output official_runs/$SID
git add official_runs/$SID
git commit -m "Add first validated claude-code-persistent run: haiku vs gpt-5-mini (HU 10)"
```

- [ ] **Step 4: Close issues #1 and #3**

```bash
COMMIT=$(git rev-parse HEAD)
gh issue close 1 --repo zgl95116-sys/HoldemAgentBench --comment \
  "Fixed by $COMMIT. Soft-interrupt keeps the persistent process alive across timeouts; verified end-to-end by tests/e2e/test_persistent_runtime.py and the 10-hand validation run committed in $COMMIT."
gh issue close 3 --repo zgl95116-sys/HoldemAgentBench --comment \
  "Fixed by <bracketed-paste commit sha>. Verified by scripts/smoke_persistent.py (single-prompt smoke) and tests/e2e/test_persistent_runtime.py (3-hand match)."
```

- [ ] **Step 5: Update README — drop the "experimental" caveat**

If the README currently flags `claude-code-persistent` as experimental or links to issue #3, remove that. The status table line `Persistent Claude Code runtime ✅` is now accurate without caveats. Optionally add a one-liner under "Architecture" noting "the persistent runtime drives prompts via bracketed paste over a PTY".

- [ ] **Step 6: Final commit and push**

```bash
git add README.md
git commit -m "Drop claude-code-persistent caveat — runtime now validated end-to-end"
git push
```

CI will pick up the new `official_runs/` entry and update `docs/data/leaderboard.json`. The 10-hand sample is still well below the 5000-hand eligibility threshold, so README's Top 5 will stay empty — that's correct, and proves the eligibility filter works.

---

## Self-Review Checklist (run before handoff)

**1. Spec coverage:** every requirement maps to a task.

| Requirement | Task |
|---|---|
| Bug #3 (PTY submit) main fix | Task 2 |
| Bug #3 fallback paths | Task 3 |
| Bug #1 (kill on timeout) | Task 5 |
| Regression test for submit | Task 4 |
| Regression test for interrupt | Task 5 step 1-4 |
| Real-binary e2e | Task 6 |
| Validation against real models | Task 7 |
| Issue closure | Task 7 step 4 |
| Documentation update | Task 7 step 5 |

**2. Placeholder scan:** no "TBD" / "implement later" / un-coded steps. Task 3 step 1c explicitly defers SDK switch to a sub-spec — that's deliberate scope-deferral, not a placeholder.

**3. Type consistency:** `interrupt(*, reason: str) -> None` declared in Task 5 step 3, called with that exact signature in Task 5 step 5 (twice). `_send` byte sequence in Task 2 step 1 matches the assertions in Task 4 step 1. Smoke script imports `PersistentClaudeProcess`, `PersistentClaudeTimeout`, and `ShimServer` — all real (verified `ShimServer.register_player`, `start`, `base_url`, `stop` exist in `src/hab/shim/server.py`).

**4. Cost / time:** Task 1 ~$0.05, Task 2 ~$0.05, Task 5 step 6 ~$0.05, Task 6 step 3 ~$0.10, Task 7 step 1 ~$0.50. Total ~$0.75 in OpenRouter spend assuming Task 2 succeeds on first try. Add ~$1 if Task 3 fallbacks are exercised. Wall time ~1-2 hours of focused work + waiting on the validation run.

---

## Risk Notes

- **Task 2 might not work.** Bracketed paste is the most-likely fix but there's no guarantee. The smoke script gives you a 30s feedback loop; iterate, don't commit fragile guesses.
- **Task 5 introduces new state.** Reusing the persistent process means `interrupt()` must reliably reset claude's in-flight task. If interrupt-then-prompt makes claude stuck (e.g. it queues both messages), watch the e2e in Task 6 — if hand 2 onwards always times out after a hand-1 timeout, interrupt isn't actually clearing claude's task. In that case, fall back to kill+pop on timeout (current behaviour) and accept that timeouts cost a respawn.
- **MCP tool exposure is a separate concern.** The plan assumes `_persistent_claude_cmd` correctly wires `--mcp-config` (it does as of 532ae08). If Task 6 step 3 fails on `total_tools >= 1` but valid actions are present, the MCP server is reachable from a one-shot claude (`claude-code` runtime works) but not the persistent one — investigate `--strict-mcp-config` interaction with `--bare`.
- **Cost cap.** If you find yourself spending more than ~$5 on this plan, stop and reassess. The smoke script is designed to catch failures cheaply; if Task 6 step 3 burns repeatedly on real models, you're past the point of "iterate" and into "rethink architecture".
