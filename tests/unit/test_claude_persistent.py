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
