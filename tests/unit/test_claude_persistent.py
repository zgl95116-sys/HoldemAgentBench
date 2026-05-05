import sys
from pathlib import Path

import pytest

from hab.orchestrator.claude_persistent import PersistentClaudeProcess


def test_ready_detection_strips_terminal_control_sequences(tmp_path: Path):
    proc = PersistentClaudeProcess(
        player_id="p",
        workspace=tmp_path,
        cmd=[sys.executable, "-c", "pass"],
        env={},
        log_path=tmp_path / "fake.log",
    )
    proc._output_tail = "\x1b[2Caccept\x1b[1Cedits\x1b[1Con"
    assert "acceptedits" in proc._plain_output_tail()


def test_send_uses_bracketed_paste(monkeypatch, tmp_path: Path):
    """_send must wrap the prompt in bracketed-paste markers and trail
    with \\r so the claude TUI treats the input as a single atomic paste.

    Without this, prompts longer than the input box width get chopped at
    line wraps and never submit cleanly — claude sees a hung input field
    and never produces an action. See Bug #3.
    """
    proc = PersistentClaudeProcess(
        player_id="p",
        workspace=tmp_path,
        cmd=[sys.executable, "-c", "pass"],
        env={},
        log_path=tmp_path / "fake.log",
    )
    proc._master_fd = 3

    writes: list[bytes] = []
    monkeypatch.setattr(
        "hab.orchestrator.claude_persistent.os.write",
        lambda fd, data: writes.append(data) or len(data),
    )
    monkeypatch.setattr(
        "hab.orchestrator.claude_persistent.time.sleep", lambda *_: None
    )

    proc._send("hello world from a long-ish prompt that may wrap")

    blob = b"".join(writes)
    assert blob.startswith(b"\x1b[200~"), f"missing paste-start: {blob[:20]!r}"
    assert b"\x1b[201~" in blob, "missing paste-end"
    assert blob.endswith(b"\r"), f"missing trailing \\r: {blob[-5:]!r}"
    assert b"hello world" in blob


@pytest.mark.parametrize(
    "raw_status_row, expected_marker",
    [
        ("⏵⏵ accept edits on (shift+tab to cycle)", "acceptedits"),
        ("⏵⏵ auto mode on (shift+tab to cycle)", "automode"),
        ("⏵⏵ don't ask on (shift+tab to cycle)", "don'task"),
        ("⏵⏵ bypass permissions on (shift+tab to cycle)", "bypasspermissions"),
        # Even if every other word is unfamiliar, the universal hint matches.
        ("⏵⏵ some-future-mode on (shift+tab to cycle)", "shift+tabtocycle"),
    ],
)
def test_plain_output_tail_yields_recognised_ready_marker(
    tmp_path: Path, raw_status_row: str, expected_marker: str
) -> None:
    """Whichever permission mode claude renders, _wait_for_ready's marker
    set must include a substring of _plain_output_tail's space-stripped
    output. If this regresses, the persistent runtime hangs at startup
    in the affected mode (fixed in Bug #4)."""
    proc = PersistentClaudeProcess(
        player_id="p",
        workspace=tmp_path,
        cmd=[sys.executable, "-c", "pass"],
        env={},
        log_path=tmp_path / "fake.log",
    )
    proc._output_tail = raw_status_row
    assert expected_marker in proc._plain_output_tail()


@pytest.mark.asyncio
async def test_persistent_claude_process_reuses_one_cli_process(tmp_path: Path):
    workspace = tmp_path / "ws"
    (workspace / "actions").mkdir(parents=True)
    script = (
        "import json, pathlib, re, sys\n"
        "sys.stdout.write('accept edits on\\n')\n"
        "sys.stdout.flush()\n"
        "for line in sys.stdin:\n"
        "    m = re.search(r'hand_id=(h_\\d+)', line)\n"
        "    if not m:\n"
        "        continue\n"
        "    pathlib.Path('actions/action.json').write_text(json.dumps({\n"
        "        'action': 'fold',\n"
        "        'hand_id': m.group(1),\n"
        "        'reason': 'fake persistent cli'\n"
        "    }))\n"
        "    sys.stdout.write('wrote ' + m.group(1) + '\\\\n')\n"
        "    sys.stdout.flush()\n"
    )
    proc = PersistentClaudeProcess(
        player_id="p",
        workspace=workspace,
        cmd=[sys.executable, "-u", "-c", script],
        env={},
        log_path=workspace / "logs" / "fake.log",
    )
    try:
        action1, meta1 = await proc.request_action(
            prompt="hand_id=h_00001",
            action_path=workspace / "actions" / "action.json",
            hand_id="h_00001",
            timeout=3,
        )
        pid1 = meta1["process_id"]
        (workspace / "actions" / "action.json").write_text("{}\n")
        action2, meta2 = await proc.request_action(
            prompt="hand_id=h_00002",
            action_path=workspace / "actions" / "action.json",
            hand_id="h_00002",
            timeout=3,
        )
        assert action1.action == "fold"
        assert action1.hand_id == "h_00001"
        assert action2.hand_id == "h_00002"
        assert meta2["process_id"] == pid1
        assert (workspace / "logs" / "fake.log").exists()
    finally:
        await proc.close(kill=True)
