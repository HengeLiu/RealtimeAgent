from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.output import AssistantTextDelta, OutputService
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk
from audio_chat.agent_core.base import AgentEventBuffer, AgentCoreEvent
from audio_chat.agent_core.providers import (
    AsrProviderConfig,
    TEXT_AGENT_SYSTEM_PROMPT,
    TextModelProviderConfig,
    build_asr_provider,
    build_text_model,
)
from audio_chat.errors import ErrorCode
from audio_chat.tools import ToolError, ToolGateway, ToolResult


class AsrPipeline:
    def __init__(self, *, config: AsrProviderConfig, recorder: RunRecorder) -> None:
        self.provider, downgrade_reason = build_asr_provider(config)
        self.recorder = recorder
        if downgrade_reason:
            self._record_degradation(downgrade_reason)

    def append_audio(self, chunk: StreamChunk) -> str | None:
        final_text: str | None = None
        for event in self.provider.append_audio(chunk):
            name = "input_transcript.done" if event.final else "input_transcript.delta"
            self.recorder.record_agent_event(
                chunk.session_id,
                {
                    "event": name,
                    "text": event.text,
                    "provider": self.provider.provider_name,
                    "model": self.provider.model,
                },
            )
            if event.final:
                final_text = event.text
        return final_text

    def cancel(self) -> None:
        self.provider.cancel()

    def _record_degradation(self, reason: str) -> None:
        self.recorder.record_system_event(
            {"event": "system.degradation.raised", "component": "AsrPipeline", "reason": reason}
        )


class TextOutputAdapter:
    def __init__(self, *, output_service: OutputService, recorder: RunRecorder) -> None:
        self.output_service = output_service
        self.recorder = recorder

    def emit_text_delta(self, *, user_id: str, session_id: str, text: str, final: bool = False) -> None:
        self.recorder.record_agent_event(
            session_id,
            {"event": "assistant_text.delta", "text": text, "final": final},
        )
        self.output_service.on_assistant_text_delta(
            AssistantTextDelta(user_id=user_id, session_id=session_id, text=text, final=final)
        )


class TextAgentCore:
    def __init__(
        self,
        *,
        control_service: ControlService,
        output_service: OutputService,
        recorder: RunRecorder,
        asr_config: AsrProviderConfig | None = None,
        text_model_config: TextModelProviderConfig | None = None,
        tool_gateway: ToolGateway | None = None,
    ) -> None:
        self.control_service = control_service
        self.output_service = output_service
        self.output_adapter = TextOutputAdapter(output_service=output_service, recorder=recorder)
        self.recorder = recorder
        self.asr_pipeline = AsrPipeline(config=asr_config or AsrProviderConfig(), recorder=recorder)
        self.text_model, downgrade_reason = build_text_model(text_model_config or TextModelProviderConfig())
        if downgrade_reason:
            self.recorder.record_system_event(
                {"event": "system.degradation.raised", "component": "TextModelAdapter", "reason": downgrade_reason}
            )
        self._responded_input_streams: set[str] = set()
        self._cancelled_users: set[str] = set()
        self._event_buffer = AgentEventBuffer()
        self.tool_gateway = tool_gateway

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定 TextAgentCore 使用的 ToolGateway。

        主要逻辑：App 在完成工具发现、内置工具注册和策略配置后注入网关；
        Agent Core 只依赖网关，不直接 import 业务 Tool。
        参数：`tool_gateway` 为 SDK 统一工具网关。
        返回值：无。
        异常情况：无。
        """

        self.tool_gateway = tool_gateway

    def open(self, user_id: str, session_id: str) -> None:
        """打开文本 Agent 会话。

        主要逻辑：文本链路不需要提前连接模型 provider，只记录统一会话事件。
        参数：`user_id` 为用户标识，`session_id` 为当前会话。
        返回值：无。
        异常情况：无。
        """

        self._record_event("session.opened", user_id=user_id, session_id=session_id, agent_core="TextAgentCore")

    def append_audio_event(self, chunk: StreamChunk) -> None:
        transcript = self.asr_pipeline.append_audio(chunk)
        turn_key = chunk.stream_id or chunk.session_id
        if transcript is None or turn_key in self._responded_input_streams:
            return
        self._responded_input_streams.add(turn_key)
        self._cancelled_users.discard(chunk.user_id)
        self.control_service.append_message(
            chunk.user_id,
            {
                "session_id": chunk.session_id,
                "role": "user",
                "content": transcript,
                "event": "input_transcript.done",
            },
        )
        self.control_service.publish(
            Event(
                event_name="agent.response.started",
                user_id=chunk.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=chunk.session_id,
                payload={"agent_core": "TextAgentCore"},
            )
        )
        self._record_event(
            "response.started",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            agent_core="TextAgentCore",
        )
        assistant_text = self._run_tool_loop(
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            transcript=transcript,
        )
        self.output_adapter.emit_text_delta(user_id=chunk.user_id, session_id=chunk.session_id, text="", final=True)
        self.control_service.append_message(
            chunk.user_id,
            {
                "session_id": chunk.session_id,
                "role": "assistant",
                "content": assistant_text,
                "event": "assistant_text.done",
            },
        )
        self.control_service.publish(
            Event(
                event_name="agent.response.completed",
                user_id=chunk.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=chunk.session_id,
                payload={"agent_core": "TextAgentCore"},
            )
        )
        self._record_event(
            "response.done",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            agent_core="TextAgentCore",
            assistant_text=assistant_text,
        )

    def _run_tool_loop(self, *, user_id: str, session_id: str, transcript: str) -> str:
        """运行 Text Agent 工具循环。

        主要逻辑：支持 provider 通过 `stream_messages(messages, tools)` 返回
        `tool_call` 事件；每次 ToolResult 会写入消息历史，然后继续请求模型生成后续回复。
        参数：`user_id/session_id/transcript` 定位当前轮次。
        返回值：最终助手文本。
        异常情况：ToolGateway 会把工具异常转换为 ToolResult，本函数不抛业务异常。
        """

        messages: list[dict[str, Any]] = [{"role": "user", "content": transcript}]
        assistant_parts: list[str] = []
        tools = self.tool_gateway.provider_schemas() if self.tool_gateway is not None else []
        self.recorder.record_model_request(
            session_id,
            {
                "provider": getattr(self.text_model, "provider_name", "unknown"),
                "model": getattr(self.text_model, "model", "unknown"),
                "runner": "agent_core_text",
                "user_id": user_id,
                "session_id": session_id,
                "system_prompt": TEXT_AGENT_SYSTEM_PROMPT,
                "messages": [{"role": "system", "content": TEXT_AGENT_SYSTEM_PROMPT}, *list(messages)],
                "tools": tools,
                "tool_count": len(tools),
            },
        )
        for _ in range(4):
            tool_calls: list[dict[str, Any]] = []
            model_output_started = False
            first_output_was_tool_call = False
            for item in self._stream_model(messages=messages, transcript=transcript, tools=tools):
                if user_id in self._cancelled_users:
                    return "".join(assistant_parts)
                if isinstance(item, dict) and item.get("type") == "tool_call":
                    if not model_output_started:
                        model_output_started = True
                        first_output_was_tool_call = True
                    tool_calls.append(item)
                    self._record_event(
                        "tool_call.delta",
                        user_id=user_id,
                        session_id=session_id,
                        tool_call_id=str(item.get("id") or ""),
                        tool_name=str(item.get("name") or ""),
                    )
                    continue
                text_delta = self._extract_text_delta(item)
                if not text_delta:
                    continue
                if not model_output_started:
                    model_output_started = True
                assistant_parts.append(text_delta)
                self.output_adapter.emit_text_delta(
                    user_id=user_id,
                    session_id=session_id,
                    text=text_delta,
                    final=False,
                )
            if not tool_calls or self.tool_gateway is None:
                break
            messages.append({"role": "assistant", "content": "", "tool_calls": tool_calls})
            for tool_call in tool_calls:
                if first_output_was_tool_call:
                    self.tool_gateway.emit_progress_once(
                        name=str(tool_call.get("name") or ""),
                        user_id=user_id,
                        session_id=session_id,
                        output_service=self.output_service,
                    )
                result = self._call_tool(
                    name=str(tool_call.get("name") or ""),
                    user_id=user_id,
                    session_id=session_id,
                    input_data=dict(tool_call.get("arguments") or {}),
                )
                result_dict = self._tool_result_to_dict(result)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": tool_call.get("name"),
                        "content": result_dict,
                    }
                )
                self.control_service.append_message(
                    user_id,
                    {
                        "session_id": session_id,
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "name": tool_call.get("name"),
                        "content": result_dict,
                        "event": "tool.result",
                    },
                )
        return "".join(assistant_parts)

    def _stream_model(self, *, messages: list[dict[str, Any]], transcript: str, tools: list[dict]) -> Any:
        stream_messages = getattr(self.text_model, "stream_messages", None)
        if callable(stream_messages):
            return stream_messages(messages=messages, tools=tools)
        return self.text_model.stream_text(transcript)

    @staticmethod
    def _extract_text_delta(item: Any) -> str:
        if isinstance(item, str):
            return item
        if isinstance(item, dict):
            return str(item.get("delta") or item.get("text") or "")
        return str(item or "")

    def _call_tool(self, *, name: str, user_id: str, session_id: str, input_data: dict) -> ToolResult:
        if self.tool_gateway is None:
            return ToolResult.failed(ToolError("tool gateway is not configured", code=ErrorCode.PROTOCOL_ERROR))
        return self.tool_gateway.call_sync_safe(
            name=name,
            user_id=user_id,
            session_id=session_id,
            input_data=input_data,
        )

    @staticmethod
    def _tool_result_to_dict(result: ToolResult) -> dict:
        return {
            "ok": result.ok,
            "data": TextAgentCore._jsonable(result.data),
            "message": result.message,
            "assets": [TextAgentCore._jsonable(item) for item in result.assets or []],
            "artifacts": [TextAgentCore._jsonable(item) for item in result.artifacts or []],
            "tasks": [TextAgentCore._jsonable(item) for item in result.tasks or []],
            "meta": result.meta or {},
            "error": result.error,
        }

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, (list, tuple)):
            return [TextAgentCore._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {key: TextAgentCore._jsonable(item) for key, item in value.items()}
        return value

    def commit_input(self, user_id: str, session_id: str, *, reason: str = "endpoint_commit") -> None:
        """提交文本 Agent 输入边界。

        主要逻辑：当前 ASR provider 以 final chunk 作为真实 turn boundary；显式
        commit 先作为公共接口和事件快照，后续可接 server VAD 或端侧提交事件。
        参数：`user_id`、`session_id` 定位会话；`reason` 标识提交来源。
        返回值：无。
        异常情况：无。
        """

        self._record_event("input.committed", user_id=user_id, session_id=session_id, reason=reason)

    def interrupt(self, user_id: str, *, reason: str) -> None:
        self._cancelled_users.add(user_id)
        self.asr_pipeline.cancel()
        self.text_model.cancel()
        self.recorder.record_agent_event(
            "interruptions",
            {"event": "agent.response.cancelled", "user_id": user_id, "reason": reason},
        )
        self._record_event("response.cancelled", user_id=user_id, reason=reason)

    def close(self, user_id: str, *, reason: str) -> None:
        """关闭文本 Agent 会话。

        主要逻辑：取消 ASR 和文本模型，清理当前用户取消标记，并记录统一关闭事件。
        参数：`user_id` 为用户标识；`reason` 为关闭原因。
        返回值：无。
        异常情况：provider cancel 异常由 provider 自身处理。
        """

        self._cancelled_users.discard(user_id)
        self.asr_pipeline.cancel()
        self.text_model.cancel()
        self._record_event("session.closed", user_id=user_id, reason=reason)

    def events(self) -> list[AgentCoreEvent]:
        """返回文本 Agent 统一事件快照。"""

        return self._event_buffer.events()

    def _record_event(self, event: str, *, user_id: str = "", session_id: str = "", **payload) -> None:
        """同时写入内存事件和 runs 产物。

        参数：
        1. `event`：统一 Agent 事件名。
        2. `user_id`：用户编号。
        3. `session_id`：会话编号。
        4. `payload`：补充字段。

        返回值：无。
        异常情况：无。
        """

        self._event_buffer.record_event(event, user_id=user_id, session_id=session_id, payload=payload)
        if session_id:
            self.recorder.record_agent_event(session_id, {"event": event, "user_id": user_id, **payload})
