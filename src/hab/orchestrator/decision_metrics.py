"""Decision-level harness telemetry and scoring.

These metrics deliberately measure the agent harness, not poker strength:
whether the model returns a legal action, obeys the file protocol, finishes
within the clock, and avoids permission/tooling failures.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any

DECISION_SCHEMA_VERSION = "hab.decision.v1"
DECISION_SUMMARY_SCHEMA_VERSION = "hab.decision_summary.v1"


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _rate(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = math.ceil(0.95 * len(ordered)) - 1
    return ordered[max(0, min(idx, len(ordered) - 1))]


def _is_valid(record: dict[str, Any]) -> bool:
    if "engine_valid" in record:
        return record.get("engine_valid") is True
    return record.get("outcome") == "valid_action"


def summarize_model_decisions(records: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(records)
    outcomes = Counter(str(r.get("outcome") or "unknown") for r in records)
    valid_actions = sum(1 for r in records if _is_valid(r))
    invalid_actions = sum(
        1
        for r in records
        if r.get("outcome") == "invalid_action"
        or r.get("engine_valid") is False
    )
    timeouts = outcomes.get("timeout", 0)
    spawn_failed = outcomes.get("spawn_failed", 0)
    no_outputs = outcomes.get("no_output", 0)
    bad_json = outcomes.get("bad_json", 0)
    protocol_errors = spawn_failed + no_outputs + bad_json

    elapsed = [
        float(r["elapsed_sec"])
        for r in records
        if isinstance(r.get("elapsed_sec"), (int, float))
    ]
    timeout_fractions = [
        min(1.0, float(r["timeout_fraction"]))
        for r in records
        if isinstance(r.get("timeout_fraction"), (int, float))
    ]
    avg_elapsed = sum(elapsed) / len(elapsed) if elapsed else None
    avg_timeout_fraction = (
        sum(timeout_fractions) / len(timeout_fractions)
        if timeout_fractions
        else None
    )
    latency_score = 1.0 - min(1.0, avg_timeout_fraction or 0.0)

    write_successes = sum(1 for r in records if r.get("write_success") is True)
    permission_errors = sum(int(r.get("permission_error_count") or 0) for r in records)
    mcp_tool_calls = sum(int(r.get("mcp_tool_call_count") or 0) for r in records)
    write_tool_calls = sum(int(r.get("write_tool_call_count") or 0) for r in records)
    declared_tool_calls = sum(len(r.get("tool_calls_used") or []) for r in records)
    tool_calls = mcp_tool_calls + declared_tool_calls

    valid_action_rate = _rate(valid_actions, n)
    timeout_rate = _rate(timeouts, n)
    protocol_error_rate = _rate(protocol_errors, n)
    write_success_rate = _rate(write_successes, n)
    permission_error_rate = min(1.0, _rate(permission_errors, n))

    harness_score = 100.0 * (
        0.40 * valid_action_rate
        + 0.20 * (1.0 - timeout_rate)
        + 0.15 * write_success_rate
        + 0.10 * (1.0 - protocol_error_rate)
        + 0.10 * latency_score
        + 0.05 * (1.0 - permission_error_rate)
    )

    return {
        "decisions": n,
        "valid_actions": valid_actions,
        "valid_action_rate": _round(valid_action_rate),
        "timeouts": timeouts,
        "timeout_rate": _round(timeout_rate),
        "spawn_failed": spawn_failed,
        "no_outputs": no_outputs,
        "bad_json": bad_json,
        "protocol_error_rate": _round(protocol_error_rate),
        "invalid_actions": invalid_actions,
        "avg_elapsed_sec": _round(avg_elapsed),
        "p95_elapsed_sec": _round(_p95(elapsed)),
        "avg_timeout_fraction": _round(avg_timeout_fraction),
        "write_successes": write_successes,
        "write_success_rate": _round(write_success_rate),
        "permission_errors": permission_errors,
        "permission_error_rate": _round(permission_error_rate),
        "tool_calls": tool_calls,
        "mcp_tool_calls": mcp_tool_calls,
        "write_tool_calls": write_tool_calls,
        "avg_tool_calls": _round(_rate(tool_calls, n)),
        "harness_score": round(harness_score, 1) if n else None,
    }


def summarize_decisions(records: list[dict[str, Any]]) -> dict[str, Any]:
    by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        model = str(record.get("model") or record.get("player_id") or "unknown")
        by_model[model].append(record)

    return {
        "schema_version": DECISION_SUMMARY_SCHEMA_VERSION,
        "decisions": len(records),
        "overall": summarize_model_decisions(records),
        "per_model": {
            model: summarize_model_decisions(model_records)
            for model, model_records in sorted(by_model.items())
        },
    }
