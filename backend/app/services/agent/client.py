"""AgentClient abstraction for C12.

The :class:`AgentClient` Protocol has one method — ``astream`` — which
yields :class:`AgentEvent` instances (text deltas, tool calls, tool
results, the terminal ``End``). Concrete implementations:

- :class:`OpenAIAgentClient` — backed by ``openai.AsyncOpenAI``'s
  ``chat.completions`` streaming API. Picked by default when
  ``AGENT_VENDOR=openai`` (the default) and ``OPENAI_API_KEY`` is
  present.
- :class:`AnthropicAgentClient` — backed by the ``anthropic`` SDK.
  Picked when ``AGENT_VENDOR=anthropic`` + ``ANTHROPIC_API_KEY``.

Why a dataclass event stream instead of raw SDK chunks? Two reasons:

1. **Vendor independence** — the call site (``chat/stream`` SSE route)
   never sees an OpenAI or Anthropic dict, so swapping vendors is a
   zero-churn operation.
2. **Tooling** — the TopicService and SSE route both consume the same
   ``AgentEvent`` stream, so unit tests can feed fake events without
   standing up a vendor client.

Both vendors expose a *native* tool-use loop (OpenAI: ``tool_calls`` on
the assistant delta; Anthropic: ``tool_use`` blocks). We surface the
tool *call* as ``ToolCall`` events and expect the caller (ToolBox + SSE
route) to actually invoke the tool and feed the ``ToolResult`` back in
via the next ``astream`` round-trip. This keeps vendor SDKs out of the
ToolBox and gives us a clean seam for cost-guards / telemetry.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from backend.app.core.config import Settings, get_settings


# ---------------------------------------------------------------------------
# Event / message shapes
# ---------------------------------------------------------------------------


Role = Literal["system", "user", "assistant", "tool"]


@dataclass(slots=True)
class ChatMessage:
    """One message the agent sees.

    ``tool_calls`` is populated on assistant messages that fanned out to
    tools (OpenAI-style). ``tool_call_id`` + ``name`` are populated on
    ``role="tool"`` messages that carry back the tool's output.
    """

    role: Role
    content: str = ""
    name: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_id: str | None = None


@dataclass(slots=True)
class ToolSpec:
    """JSON-schema'd tool spec the agent can call.

    The schema uses the OpenAI "function" shape; the Anthropic adapter
    transposes it at send-time. Keeping one source of truth (the ToolBox)
    means we don't maintain two schema catalogues.
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(slots=True)
class TextDelta:
    """A chunk of the assistant's text response."""

    text: str


@dataclass(slots=True)
class ToolCall:
    """Model is requesting that a named tool be invoked with ``arguments``.

    ``id`` is whatever the vendor gave us (OpenAI: ``call_<uuid>``;
    Anthropic: ``toolu_<id>``). The caller feeds this id back in as the
    ``tool_call_id`` of the subsequent ``role="tool"`` message.
    """

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(slots=True)
class ToolResult:
    """Caller's echo of the result of :class:`ToolCall` — surfaced for
    logging/audit. Not sent to the vendor (the next ``astream`` call
    re-sends the whole conversation including the tool response)."""

    id: str
    name: str
    output: str


@dataclass(slots=True)
class End:
    """Terminal marker. ``finish_reason`` is ``"stop"`` / ``"tool_use"`` /
    ``"length"`` — the SSE route surfaces it so the UI can react."""

    finish_reason: str
    usage: dict[str, int] = field(default_factory=dict)


AgentEvent = TextDelta | ToolCall | ToolResult | End


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AgentClient(Protocol):
    """Minimal surface every vendor adapter exposes."""

    vendor: str

    async def astream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[AgentEvent]:
        """Stream events for one model turn.

        The caller is responsible for the tool-use loop: on a
        :class:`ToolCall` event it invokes the tool, appends the
        assistant message + a ``role="tool"`` message to ``messages``,
        and calls ``astream`` again.
        """
        ...

    async def acomplete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        """Non-streaming one-shot completion.

        Used by the TopicService (summariser + topic-shift classifier)
        where streaming buys nothing and JSON-mode helps.
        """
        ...


# ---------------------------------------------------------------------------
# OpenAI impl
# ---------------------------------------------------------------------------


def _openai_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _openai_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": m.tool_call_id,
                    "name": m.name,
                    "content": m.content,
                }
            )
            continue
        entry: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.role == "assistant" and m.tool_calls:
            entry["tool_calls"] = m.tool_calls
            if not m.content:
                # OpenAI rejects assistant messages with neither content
                # nor tool_calls; send an explicit null so the vendor
                # shape matches the docs even when content is empty.
                entry["content"] = None
        out.append(entry)
    return out


class OpenAIAgentClient:
    """AgentClient backed by ``openai.AsyncOpenAI``.

    Uses ``chat.completions`` with streaming for ``astream`` and the
    same endpoint without streaming for ``acomplete``. We deliberately
    do **not** use the Responses API yet: chat.completions' tool-use
    contract is better documented and matches Anthropic's shape more
    closely, keeping the two adapters symmetric.
    """

    vendor = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        default_fast_model: str,
    ) -> None:
        # Lazy import — the SDK imports cryptography on load which we
        # don't want to pay for on workers that never touch the agent.
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key)
        self._default_model = default_model
        self._default_fast_model = default_fast_model

    async def astream(  # type: ignore[override]
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[AgentEvent]:
        return self._astream_impl(
            messages,
            tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def _astream_impl(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        *,
        model: str | None,
        max_tokens: int | None,
        temperature: float,
    ) -> AsyncIterator[AgentEvent]:
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": _openai_messages(messages),
            "temperature": temperature,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = _openai_tools(tools)
            kwargs["tool_choice"] = "auto"
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens

        stream = await self._client.chat.completions.create(**kwargs)

        # Partial tool calls are streamed as arguments-character deltas
        # indexed by the tool_call's position in the assistant turn;
        # buffer them here and emit a single ToolCall once the model
        # stops talking about that index.
        accumulated_calls: dict[int, dict[str, Any]] = {}
        finish_reason = "stop"
        usage: dict[str, int] = {}

        async for chunk in stream:
            if getattr(chunk, "usage", None):
                u = chunk.usage
                usage = {
                    "prompt_tokens": int(u.prompt_tokens or 0),
                    "completion_tokens": int(u.completion_tokens or 0),
                    "total_tokens": int(u.total_tokens or 0),
                }
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if delta.content:
                yield TextDelta(text=delta.content)
            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index
                    slot = accumulated_calls.setdefault(
                        idx,
                        {"id": "", "name": "", "arguments": ""},
                    )
                    if tc.id:
                        slot["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn:
                        if fn.name:
                            slot["name"] = fn.name
                        if fn.arguments:
                            slot["arguments"] += fn.arguments
            if choice.finish_reason:
                finish_reason = choice.finish_reason

        if accumulated_calls:
            for _, slot in sorted(accumulated_calls.items()):
                args_raw: str = slot.get("arguments") or "{}"
                import json

                try:
                    args = json.loads(args_raw) if args_raw else {}
                except json.JSONDecodeError:
                    # Salvage the raw payload so the caller can surface a
                    # tool_error rather than crashing the whole turn.
                    args = {"__raw": args_raw}
                yield ToolCall(
                    id=slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                    name=slot["name"] or "",
                    arguments=args,
                )

        yield End(finish_reason=finish_reason, usage=usage)

    async def acomplete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        kwargs: dict[str, Any] = {
            "model": model or self._default_fast_model,
            "messages": _openai_messages(messages),
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format
        resp = await self._client.chat.completions.create(**kwargs)
        return (resp.choices[0].message.content or "").strip()


# ---------------------------------------------------------------------------
# Anthropic impl
# ---------------------------------------------------------------------------


def _anthropic_tools(tools: Sequence[ToolSpec]) -> list[dict[str, Any]]:
    return [
        {
            "name": t.name,
            "description": t.description,
            "input_schema": t.parameters,
        }
        for t in tools
    ]


def _anthropic_messages(
    messages: Sequence[ChatMessage],
) -> tuple[str, list[dict[str, Any]]]:
    """Anthropic splits ``system`` out of ``messages`` into a top-level
    parameter. Concatenate any system messages and return
    ``(system, messages)`` in Anthropic shape.
    """
    system_parts: list[str] = []
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "system":
            if m.content:
                system_parts.append(m.content)
            continue
        if m.role == "tool":
            out.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": m.tool_call_id,
                            "content": m.content,
                        }
                    ],
                }
            )
            continue
        if m.role == "assistant" and m.tool_calls:
            blocks: list[dict[str, Any]] = []
            if m.content:
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls:
                fn = tc.get("function") or {}
                import json

                raw_args = fn.get("arguments")
                try:
                    parsed = (
                        json.loads(raw_args)
                        if isinstance(raw_args, str) and raw_args
                        else raw_args or {}
                    )
                except json.JSONDecodeError:
                    parsed = {"__raw": raw_args}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}",
                        "name": fn.get("name") or "",
                        "input": parsed,
                    }
                )
            out.append({"role": "assistant", "content": blocks})
            continue
        out.append({"role": m.role, "content": m.content})
    return ("\n\n".join(system_parts), out)


class AnthropicAgentClient:
    """AgentClient backed by ``anthropic.AsyncAnthropic``.

    Enabled when ``AGENT_VENDOR=anthropic`` and ``ANTHROPIC_API_KEY`` is
    set. Tools are transposed to Anthropic's ``input_schema`` shape;
    ``system`` messages are pulled out of the ``messages`` array into
    the top-level ``system`` parameter.
    """

    vendor = "anthropic"

    def __init__(
        self,
        *,
        api_key: str,
        default_model: str,
        default_fast_model: str,
    ) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._default_model = default_model
        self._default_fast_model = default_fast_model

    async def astream(  # type: ignore[override]
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] = (),
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
    ) -> AsyncIterator[AgentEvent]:
        return self._astream_impl(
            messages,
            tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    async def _astream_impl(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec],
        *,
        model: str | None,
        max_tokens: int | None,
        temperature: float,
    ) -> AsyncIterator[AgentEvent]:
        system, history = _anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": history,
            "max_tokens": max_tokens or 4096,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = _anthropic_tools(tools)

        pending_tool: dict[str, Any] | None = None
        finish_reason = "stop"
        usage: dict[str, int] = {}

        async with self._client.messages.stream(**kwargs) as stream:
            async for event in stream:
                et = getattr(event, "type", None)
                if et == "content_block_start":
                    block = getattr(event, "content_block", None)
                    if getattr(block, "type", None) == "tool_use":
                        pending_tool = {
                            "id": block.id,
                            "name": block.name,
                            "input_json": "",
                        }
                elif et == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dt = getattr(delta, "type", None)
                    if dt == "text_delta":
                        yield TextDelta(text=delta.text)
                    elif dt == "input_json_delta" and pending_tool is not None:
                        pending_tool["input_json"] += delta.partial_json
                elif et == "content_block_stop":
                    if pending_tool is not None:
                        import json

                        raw = pending_tool.pop("input_json", "") or "{}"
                        try:
                            args = json.loads(raw) if raw else {}
                        except json.JSONDecodeError:
                            args = {"__raw": raw}
                        yield ToolCall(
                            id=pending_tool["id"],
                            name=pending_tool["name"],
                            arguments=args,
                        )
                        pending_tool = None
                elif et == "message_stop":
                    # Final message has the usage + stop_reason.
                    message = getattr(stream, "current_message", None)
                    if message is not None:
                        finish_reason = getattr(message, "stop_reason", "stop") or "stop"
                        u = getattr(message, "usage", None)
                        if u is not None:
                            usage = {
                                "prompt_tokens": int(getattr(u, "input_tokens", 0) or 0),
                                "completion_tokens": int(
                                    getattr(u, "output_tokens", 0) or 0
                                ),
                                "total_tokens": int(
                                    (getattr(u, "input_tokens", 0) or 0)
                                    + (getattr(u, "output_tokens", 0) or 0)
                                ),
                            }

        # Anthropic's ``tool_use`` stop_reason is the signal that the
        # model wants us to run tools; normalise it to the OpenAI-ish
        # token the rest of the stack already checks for.
        if finish_reason == "tool_use":
            finish_reason = "tool_calls"
        elif finish_reason == "end_turn":
            finish_reason = "stop"
        yield End(finish_reason=finish_reason, usage=usage)

    async def acomplete(
        self,
        messages: Sequence[ChatMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.2,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        system, history = _anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model or self._default_fast_model,
            "messages": history,
            "max_tokens": max_tokens or 2048,
            "temperature": temperature,
        }
        if system:
            kwargs["system"] = system
        # Anthropic doesn't have ``response_format=json``; we rely on
        # prompt engineering to produce JSON and json.loads on our side.
        resp = await self._client.messages.create(**kwargs)
        parts: list[str] = []
        for block in resp.content or []:
            if getattr(block, "type", None) == "text":
                parts.append(block.text)
        return "".join(parts).strip()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def pick_default_client(settings: Settings | None = None) -> AgentClient:
    """Resolve :class:`AgentClient` implementation based on env settings.

    Default vendor is ``openai``. If ``AGENT_VENDOR=anthropic`` is set
    but ``ANTHROPIC_API_KEY`` is missing, we fall back to OpenAI with a
    warning — misconfiguration shouldn't take the whole app offline.
    """
    s = settings or get_settings()
    vendor = (s.agent_vendor or "openai").lower().strip()

    if vendor == "anthropic" and s.anthropic_api_key:
        return AnthropicAgentClient(
            api_key=s.anthropic_api_key,
            default_model=s.agent_model_main,
            default_fast_model=s.agent_model_fast,
        )

    api_key = s.openai_api_key or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "No LLM API key configured. Set OPENAI_API_KEY (or "
            "ANTHROPIC_API_KEY + AGENT_VENDOR=anthropic) to enable the "
            "real agent."
        )
    return OpenAIAgentClient(
        api_key=api_key,
        default_model=s.agent_model_main,
        default_fast_model=s.agent_model_fast,
    )


__all__ = [
    "AgentClient",
    "AgentEvent",
    "AnthropicAgentClient",
    "ChatMessage",
    "End",
    "OpenAIAgentClient",
    "TextDelta",
    "ToolCall",
    "ToolResult",
    "ToolSpec",
    "pick_default_client",
]
