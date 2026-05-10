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


def _normalize_history_message(record: dict[str, Any]) -> dict[str, Any] | None:
    """把落盘消息转换为模型可消费的历史上下文。

    主要逻辑：只回灌 `user` 和 `assistant` 的文本内容；`tool` 消息留在
    `messages.jsonl` 中审计，不直接放入新一轮模型请求，避免形成孤立工具结果。
    参数：`record` 为 `messages.jsonl` 中的一行。
    返回值：模型消息或 None。
    异常情况：字段缺失或内容为空时返回 None。
    """

    role = str(record.get("role") or "").strip()
    if role not in {"user", "assistant"}:
        return None
    content = record.get("content")
    if isinstance(content, str):
        text = " ".join(content.strip().split())
    else:
        text = ""
    if not text:
        return None
    return {"role": role, "content": text}


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
        memory_service: Any = None,
        max_context_messages: int = 30,
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
        self.memory_service = memory_service
        self.max_context_messages = max(1, int(max_context_messages or 30))

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

        messages: list[dict[str, Any]] = self._build_runtime_messages(
            user_id=user_id,
            session_id=session_id,
            transcript=transcript,
        )
        assistant_parts: list[str] = []
        tools = self.tool_gateway.provider_schemas() if self.tool_gateway is not None else []
        system_prompt = self._build_system_prompt(user_id=user_id, session_id=session_id)
        previous_system_prompt = getattr(self.text_model, "system_prompt", None)
        if previous_system_prompt is not None:
            setattr(self.text_model, "system_prompt", system_prompt)
        self.recorder.record_model_request(
            session_id,
            {
                "provider": getattr(self.text_model, "provider_name", "unknown"),
                "model": getattr(self.text_model, "model", "unknown"),
                "runner": "agent_core_text",
                "user_id": user_id,
                "session_id": session_id,
                "system_prompt": system_prompt,
                "messages": [{"role": "system", "content": system_prompt}, *list(messages)],
                "tools": tools,
                "tool_count": len(tools),
            },
        )
        try:
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
        finally:
            if previous_system_prompt is not None:
                setattr(self.text_model, "system_prompt", previous_system_prompt)
        return "".join(assistant_parts)

    def _stream_model(self, *, messages: list[dict[str, Any]], transcript: str, tools: list[dict]) -> Any:
        stream_messages = getattr(self.text_model, "stream_messages", None)
        if callable(stream_messages):
            return stream_messages(messages=messages, tools=tools)
        return self.text_model.stream_text(transcript)

    def _build_runtime_messages(self, *, user_id: str, session_id: str, transcript: str) -> list[dict[str, Any]]:
        """构造发送给文本模型的运行时消息。

        主要逻辑：从同一 `user_id + session_id(device_id)` 的 `messages.jsonl`
        读取历史 user/assistant 对话文本，再确保当前用户输入位于最后。历史 tool
        消息不直接回灌给模型，避免缺少配套 assistant tool_calls 时触发 provider 协议错误。
        参数：`user_id/session_id` 定位历史文件，`transcript` 为当前轮用户文本。
        返回值：Chat Completions 风格的消息列表，不包含 system message。
        异常情况：历史读取失败时退化为仅包含当前用户输入。
        """

        history: list[dict[str, Any]] = []
        try:
            history = self.control_service.load_messages(
                user_id=user_id,
                session_id=session_id,
                limit=self.max_context_messages,
            )
        except Exception:
            history = []
        messages = [_normalize_history_message(item) for item in history]
        messages = [item for item in messages if item is not None]
        current = {"role": "user", "content": transcript}
        if not messages or messages[-1] != current:
            messages.append(current)
        if len(messages) > self.max_context_messages:
            messages = messages[-self.max_context_messages :]
        return messages

    def _build_system_prompt(self, *, user_id: str, session_id: str) -> str:
        """构造当前轮文本模型 system prompt。

        主要逻辑：在静态提示词后追加长期记忆和更早历史摘要，避免把已压缩的原始
        对话重复放回 messages。
        参数：`user_id` 为当前用户编号，`session_id` 为设备级会话编号。
        返回值：发送给文本模型的 system prompt。
        异常情况：memory 或历史摘要读取失败时跳过对应片段。
        """

        parts = [str(getattr(self.text_model, "system_prompt", TEXT_AGENT_SYSTEM_PROMPT))]
        memory = self.memory_service
        if memory is not None and getattr(memory, "enabled", False):
            try:
                fragment = memory.build_prompt_fragment(user_id=user_id)
            except Exception:
                fragment = ""
            if fragment:
                parts.append(fragment)
        try:
            summary_fragment = self.control_service.load_message_summary_fragment(
                user_id=user_id,
                session_id=session_id,
            )
        except Exception:
            summary_fragment = ""
        if summary_fragment:
            parts.append(summary_fragment)
        return "\n\n".join(parts)

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
