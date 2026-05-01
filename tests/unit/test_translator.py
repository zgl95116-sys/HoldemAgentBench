from hab.shim.translator import (
    anthropic_request_to_openai,
    openai_response_to_anthropic,
)


def test_request_basic_text():
    req = {
        "model": "openai/gpt-5",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = anthropic_request_to_openai(req)
    assert out["model"] == "openai/gpt-5"
    assert out["max_tokens"] == 1024
    assert out["messages"] == [{"role": "user", "content": "hi"}]


def test_request_system_prompt_extracted():
    req = {
        "model": "x/y",
        "max_tokens": 100,
        "system": "you are a poker bot",
        "messages": [{"role": "user", "content": "hi"}],
    }
    out = anthropic_request_to_openai(req)
    assert out["messages"][0] == {"role": "system", "content": "you are a poker bot"}
    assert out["messages"][1] == {"role": "user", "content": "hi"}


def test_request_content_blocks_flattened():
    req = {
        "model": "x/y",
        "max_tokens": 100,
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": "hello"}],
        }],
    }
    out = anthropic_request_to_openai(req)
    assert out["messages"][0]["content"] == "hello"


def test_request_tool_use_assistant_message():
    req = {
        "model": "x/y",
        "max_tokens": 100,
        "messages": [{
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me compute"},
                {"type": "tool_use", "id": "tool_1", "name": "equity_calculator", "input": {"my_cards": ["As", "Kh"]}},
            ],
        }],
    }
    out = anthropic_request_to_openai(req)
    msg = out["messages"][0]
    assert msg["role"] == "assistant"
    assert msg["tool_calls"][0]["id"] == "tool_1"
    assert msg["tool_calls"][0]["function"]["name"] == "equity_calculator"


def test_request_tool_result_becomes_tool_message():
    req = {
        "model": "x/y",
        "max_tokens": 100,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "tool_1", "content": "equity=0.62"},
            ],
        }],
    }
    out = anthropic_request_to_openai(req)
    assert out["messages"][0]["role"] == "tool"
    assert out["messages"][0]["tool_call_id"] == "tool_1"
    assert out["messages"][0]["content"] == "equity=0.62"


def test_request_tools_schema_translated():
    req = {
        "model": "x/y",
        "max_tokens": 100,
        "messages": [{"role": "user", "content": "hi"}],
        "tools": [
            {
                "name": "equity_calculator",
                "description": "compute equity",
                "input_schema": {"type": "object", "properties": {"my_cards": {"type": "array"}}},
            }
        ],
    }
    out = anthropic_request_to_openai(req)
    assert out["tools"][0]["type"] == "function"
    assert out["tools"][0]["function"]["name"] == "equity_calculator"


def test_response_basic_text():
    openai_resp = {
        "id": "chatcmpl-1",
        "model": "openai/gpt-5",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": "hello there"},
            "finish_reason": "stop",
        }],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }
    out = openai_response_to_anthropic(openai_resp)
    assert out["type"] == "message"
    assert out["role"] == "assistant"
    assert out["content"] == [{"type": "text", "text": "hello there"}]
    assert out["stop_reason"] == "end_turn"
    assert out["usage"]["input_tokens"] == 10
    assert out["usage"]["output_tokens"] == 5


def test_response_tool_use():
    openai_resp = {
        "id": "x",
        "model": "m",
        "choices": [{
            "index": 0,
            "message": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "equity_calculator", "arguments": '{"a": 1}'},
                }],
            },
            "finish_reason": "tool_calls",
        }],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
    out = openai_response_to_anthropic(openai_resp)
    assert out["stop_reason"] == "tool_use"
    tool_block = out["content"][0]
    assert tool_block["type"] == "tool_use"
    assert tool_block["id"] == "call_1"
    assert tool_block["name"] == "equity_calculator"
    assert tool_block["input"] == {"a": 1}
