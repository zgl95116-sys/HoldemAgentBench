from hab.orchestrator.decision_metrics import summarize_decisions


def test_decision_summary_scores_reliable_agent_high():
    summary = summarize_decisions([
        {
            "player_id": "a",
            "model": "model/a",
            "outcome": "valid_action",
            "engine_valid": True,
            "elapsed_sec": 2.0,
            "timeout_fraction": 0.1,
            "write_success": True,
            "tool_calls_used": ["gto_lookup"],
            "mcp_tool_call_count": 1,
            "write_tool_call_count": 1,
            "permission_error_count": 0,
        },
        {
            "player_id": "a",
            "model": "model/a",
            "outcome": "valid_action",
            "engine_valid": True,
            "elapsed_sec": 4.0,
            "timeout_fraction": 0.2,
            "write_success": True,
            "tool_calls_used": [],
            "mcp_tool_call_count": 0,
            "write_tool_call_count": 1,
            "permission_error_count": 0,
        },
    ])

    model_summary = summary["per_model"]["model/a"]
    assert model_summary["decisions"] == 2
    assert model_summary["valid_action_rate"] == 1.0
    assert model_summary["timeout_rate"] == 0.0
    assert model_summary["write_success_rate"] == 1.0
    assert model_summary["harness_score"] > 95


def test_decision_summary_penalizes_timeout_and_protocol_errors():
    summary = summarize_decisions([
        {
            "player_id": "a",
            "model": "model/a",
            "outcome": "timeout",
            "engine_valid": False,
            "elapsed_sec": 90.0,
            "timeout_fraction": 1.0,
            "write_success": False,
            "permission_error_count": 1,
        },
        {
            "player_id": "a",
            "model": "model/a",
            "outcome": "bad_json",
            "engine_valid": False,
            "elapsed_sec": 10.0,
            "timeout_fraction": 0.2,
            "write_success": True,
            "permission_error_count": 0,
        },
    ])

    model_summary = summary["per_model"]["model/a"]
    assert model_summary["valid_actions"] == 0
    assert model_summary["timeouts"] == 1
    assert model_summary["bad_json"] == 1
    assert model_summary["protocol_error_rate"] == 0.5
    assert model_summary["harness_score"] < 50
