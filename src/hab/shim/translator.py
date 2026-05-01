"""Anthropic /v1/messages <-> OpenAI /v1/chat/completions format translator."""
from __future__ import annotations

import json
from typing import Any


def anthropic_request_to_openai(req: dict) -> dict:
    out: dict[str, Any] = {
        "model": req["model"],
        "max_tokens": req.get("max_tokens", 4096),
    }
    messages: list[dict] = []
    if req.get("system"):
        messages.append({"role": "system", "content": req["system"]})

    for msg in req.get("messages", []):
        role = msg["role"]
        content = msg.get("content", "")
        if isinstance(content, list):
            tool_calls: list[dict] = []
            text_parts: list[str] = []
            tool_results: list[tuple[str, str]] = []
            for block in content:
                btype = block.get("type")
                if btype == "text":
                    text_parts.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_calls.append({
                        "id": block["id"],
                        "type": "function",
                        "function": {
                            "name": block["name"],
                            "arguments": json.dumps(block.get("input", {})),
                        },
                    })
                elif btype == "tool_result":
                    tc_id = block["tool_use_id"]
                    tc_content = block.get("content", "")
                    if isinstance(tc_content, list):
                        tc_content = "".join(
                            b.get("text", "") for b in tc_content if b.get("type") == "text"
                        )
                    tool_results.append((tc_id, tc_content))
            text = "".join(text_parts) if text_parts else None
            if tool_calls:
                m: dict = {"role": role, "content": text}
                m["tool_calls"] = tool_calls
                messages.append(m)
            elif tool_results:
                for tc_id, tc_content in tool_results:
                    messages.append(
                        {"role": "tool", "tool_call_id": tc_id, "content": tc_content}
                    )
            else:
                messages.append({"role": role, "content": text or ""})
        else:
            messages.append({"role": role, "content": content})

    out["messages"] = messages

    if req.get("tools"):
        out["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("input_schema", {"type": "object"}),
                },
            }
            for t in req["tools"]
        ]
    if req.get("temperature") is not None:
        out["temperature"] = req["temperature"]
    return out


_FINISH_REASON_MAP = {
    "stop": "end_turn",
    "length": "max_tokens",
    "tool_calls": "tool_use",
    "content_filter": "stop_sequence",
}


def openai_response_to_anthropic(resp: dict) -> dict:
    choice = resp["choices"][0]
    msg = choice["message"]
    finish = choice.get("finish_reason", "stop")

    content_blocks: list[dict] = []
    if msg.get("content"):
        content_blocks.append({"type": "text", "text": msg["content"]})
    for tc in msg.get("tool_calls") or []:
        try:
            args = json.loads(tc["function"]["arguments"])
        except (json.JSONDecodeError, TypeError):
            args = {}
        content_blocks.append({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["function"]["name"],
            "input": args,
        })

    usage = resp.get("usage", {})
    return {
        "id": resp.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": resp.get("model", ""),
        "content": content_blocks,
        "stop_reason": _FINISH_REASON_MAP.get(finish, "end_turn"),
        "stop_sequence": None,
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
        },
    }
