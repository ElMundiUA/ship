"""Unit tests for :mod:`backend.app.services.agent.client`.

We don't hit real OpenAI or Anthropic here — those are tested end
to end in the SSE integration test with recorded cassettes (Day-6
polish). What we do test is the *shape transforms*: converting our
:class:`ChatMessage` / :class:`ToolSpec` dataclasses into each
vendor's wire format. A regression there is the most common way to
break tool use silently, so the unit tests fence that off.
"""

from __future__ import annotations

from backend.app.services.agent.client import (
    ChatMessage,
    ToolSpec,
    _anthropic_messages,
    _anthropic_tools,
    _anthropic_usage_dict,
    _mark_last_block_cacheable,
    _openai_messages,
    _openai_tools,
)


def test_openai_tools_wraps_in_function_shape() -> None:
    spec = ToolSpec(
        name="search_kb",
        description="search",
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
    )
    out = _openai_tools([spec])
    assert out == [
        {
            "type": "function",
            "function": {
                "name": "search_kb",
                "description": "search",
                "parameters": spec.parameters,
            },
        }
    ]


def test_anthropic_tools_uses_input_schema() -> None:
    """Phase 2 injects Anthropic's server-side ``web_search_20250305``
    in front of every custom tool list, so the test pins both the
    custom-spec transposition AND the always-on server tool."""
    spec = ToolSpec(
        name="search_kb",
        description="search",
        parameters={"type": "object", "properties": {}},
    )
    out = _anthropic_tools([spec])
    assert out == [
        {
            "type": "web_search_20250305",
            "name": "web_search",
            "max_uses": 5,
        },
        {
            "name": "search_kb",
            "description": "search",
            "input_schema": spec.parameters,
        },
    ]


def test_openai_messages_assistant_tool_calls() -> None:
    msgs = [
        ChatMessage(role="system", content="sys"),
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": "{}"},
                }
            ],
        ),
        ChatMessage(
            role="tool",
            tool_call_id="call_1",
            name="search",
            content='{"results": []}',
        ),
    ]
    out = _openai_messages(msgs)
    # Assistant with only tool calls should serialise with explicit
    # ``content=None`` per the OpenAI docs.
    assistant = next(m for m in out if m["role"] == "assistant")
    assert assistant["content"] is None
    assert assistant["tool_calls"][0]["id"] == "call_1"
    # Tool message carries the tool_call_id + name.
    tool_msg = next(m for m in out if m["role"] == "tool")
    assert tool_msg["tool_call_id"] == "call_1"
    assert tool_msg["name"] == "search"


def test_anthropic_messages_splits_system_and_rewrites_tool_use() -> None:
    msgs = [
        ChatMessage(role="system", content="sys one"),
        ChatMessage(role="system", content="sys two"),
        ChatMessage(role="user", content="hi"),
        ChatMessage(
            role="assistant",
            content="let me check",
            tool_calls=[
                {
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q": "x"}'},
                }
            ],
        ),
        ChatMessage(
            role="tool",
            tool_call_id="toolu_1",
            name="search",
            content='{"results": []}',
        ),
    ]
    system, out = _anthropic_messages(msgs)
    assert "sys one" in system and "sys two" in system
    # No "system" role messages survive; they've been moved to the
    # top-level system string.
    assert all(m["role"] != "system" for m in out)
    assistant = next(m for m in out if m["role"] == "assistant")
    assert isinstance(assistant["content"], list)
    types = [block["type"] for block in assistant["content"]]
    assert "tool_use" in types
    # The tool result is projected into a ``user`` role message with
    # a ``tool_result`` block.
    tool_result_msg = out[-1]
    assert tool_result_msg["role"] == "user"
    assert tool_result_msg["content"][0]["type"] == "tool_result"
    assert tool_result_msg["content"][0]["tool_use_id"] == "toolu_1"


def test_mark_last_block_cacheable_upgrades_string_content() -> None:
    """String-content messages get rewritten to a single text block so
    ``cache_control`` (which is per-block) has somewhere to land."""
    msg = {"role": "user", "content": "hello"}
    _mark_last_block_cacheable(msg)
    assert msg["content"] == [
        {
            "type": "text",
            "text": "hello",
            "cache_control": {"type": "ephemeral"},
        }
    ]


def test_mark_last_block_cacheable_stamps_last_block_in_list() -> None:
    msg = {
        "role": "user",
        "content": [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ],
    }
    _mark_last_block_cacheable(msg)
    assert "cache_control" not in msg["content"][0]
    assert msg["content"][1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_usage_dict_surfaces_cache_counters() -> None:
    class FakeUsage:
        input_tokens = 120
        output_tokens = 40
        cache_read_input_tokens = 800
        cache_creation_input_tokens = 200

    usage = _anthropic_usage_dict(FakeUsage())
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 40
    # Total rolls up every input class so cost dashboards see the real
    # work — uncached + cache reads + cache writes + output.
    assert usage["total_tokens"] == 120 + 40 + 800 + 200
    assert usage["cache_read_input_tokens"] == 800
    assert usage["cache_creation_input_tokens"] == 200


def test_anthropic_usage_dict_handles_missing_cache_fields() -> None:
    """Older SDK versions / non-cache responses omit the cache fields;
    the projection must default them to zero rather than crashing."""

    class FakeUsage:
        input_tokens = 50
        output_tokens = 10

    usage = _anthropic_usage_dict(FakeUsage())
    assert usage["cache_read_input_tokens"] == 0
    assert usage["cache_creation_input_tokens"] == 0
    assert usage["total_tokens"] == 60
