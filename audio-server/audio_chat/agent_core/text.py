from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from typing import Any

from audio_chat.control import ControlService
from audio_chat.observability import RunRecorder
from audio_chat.output import AssistantTextDelta, OutputService
from audio_chat.protocol import SERVER_PRODUCER_ID, Event, StreamChunk
from audio_chat.agent_core.base import AgentEventBuffer, AgentCoreEvent
from audio_chat.agent_core.context import ContextCompileRequest, ContextCompiler, record_context_events
from audio_chat.agent_core.multimodal import ModelMessageManager, MultimodalMessagePolicy
from audio_chat.agent_core.providers import (
    AsrProviderAdapter,
    AsrProviderConfig,
    TEXT_AGENT_SYSTEM_PROMPT,
    TextModelProviderConfig,
    build_asr_provider,
    build_text_model,
)
from audio_chat.agent_core.recovery import DEFAULT_RECOVERABLE_ERROR_MESSAGE, record_agent_recovery_error
from audio_chat.errors import ErrorCode
from audio_chat.tools import ToolError, ToolGateway, ToolResult


def _normalize_history_message(record: dict[str, Any]) -> dict[str, Any] | None:
    """把落盘消息转换为模型可消费的历史上下文。

    主要逻辑：回灌 `user/assistant` 文本，并保留成对工具历史中的 assistant
    `tool_calls` 与 `tool` result，避免后续轮次只看到普通助手文本。
    参数：`record` 为 `messages.jsonl` 中的一行。
    返回值：模型消息或 None。
    异常情况：字段缺失或内容为空时返回 None。
    """

    role = str(record.get("role") or "").strip()
    if role == "assistant" and isinstance(record.get("tool_calls"), list):
        tool_calls = []
        for item in record.get("tool_calls") or []:
            if not isinstance(item, dict):
                continue
            call_id = str(item.get("id") or item.get("tool_call_id") or "").strip()
            name = str(item.get("name") or "").strip()
            arguments = item.get("arguments") or {}
            if not call_id or not name:
                continue
            if not isinstance(arguments, str):
                arguments = json.dumps(arguments, ensure_ascii=False, default=str)
            tool_calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                }
            )
        if tool_calls:
            return {"role": "assistant", "content": "", "tool_calls": tool_calls}
    if role == "tool":
        tool_call_id = str(record.get("tool_call_id") or "").strip()
        if not tool_call_id:
            return None
        content = record.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
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


def _provider_tool_call_message(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """把 SDK 内部工具调用记录转换为模型 provider 可消费的 assistant 消息。

    主要逻辑：TextModelAdapter 对外统一输出扁平的 `tool_call` 字典，便于
    ToolGateway 调用；Chat Completions 兼容接口要求回填到历史消息时使用
    `tool_calls[].function` 嵌套结构，且 arguments 必须是 JSON 字符串。
    参数：`tool_calls` 为本轮模型输出的内部工具调用列表。
    返回值：可直接传给文本模型 provider 的 assistant 消息。
    异常情况：arguments 无法 JSON 序列化时退化为字符串包装，保留排障信息。
    """

    provider_calls: list[dict[str, Any]] = []
    for index, tool_call in enumerate(tool_calls):
        arguments = tool_call.get("arguments") or {}
        try:
            arguments_text = json.dumps(arguments, ensure_ascii=False)
        except TypeError:
            arguments_text = json.dumps({"_raw_arguments": str(arguments)}, ensure_ascii=False)
        provider_calls.append(
            {
                "id": str(tool_call.get("id") or f"tool_call_{index}"),
                "type": "function",
                "function": {
                    "name": str(tool_call.get("name") or ""),
                    "arguments": arguments_text,
                },
            }
        )
    return {"role": "assistant", "content": "", "tool_calls": provider_calls}


def _audit_tool_calls(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """生成可落盘并可回灌的工具调用审计记录。

    主要逻辑：保留 provider 重放所需的 `id/name/arguments`，避免把后续轮次的
    工具结果变成孤立文本。参数不可 JSON 序列化时降级为字符串包装。
    参数：`tool_calls` 为 SDK 内部工具调用列表。
    返回值：简化后的工具调用列表。
    异常情况：无。
    """

    records: list[dict[str, Any]] = []
    for index, tool_call in enumerate(tool_calls):
        arguments = tool_call.get("arguments") or {}
        try:
            json.dumps(arguments, ensure_ascii=False)
        except TypeError:
            arguments = {"_raw_arguments": str(arguments)}
        records.append(
            {
                "id": str(tool_call.get("id") or f"tool_call_{index}"),
                "name": str(tool_call.get("name") or ""),
                "arguments": arguments,
            }
        )
    return records


def _provider_tool_result_message(*, tool_call: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    """把工具执行结果转换为模型 provider 可消费的 tool 消息。

    主要逻辑：Chat Completions 兼容接口要求 tool 消息 content 是字符串；SDK
    内部和落盘审计仍保留结构化 ToolResult，便于测试和排障。
    参数：`tool_call` 为对应工具调用；`result` 为结构化工具结果。
    返回值：可直接放入模型 messages 的 tool 消息。
    异常情况：result 无法 JSON 序列化时退化为字符串包装。
    """

    try:
        content = json.dumps(result, ensure_ascii=False)
    except TypeError:
        content = json.dumps({"_raw_result": str(result)}, ensure_ascii=False)
    return {
        "role": "tool",
        "tool_call_id": str(tool_call.get("id") or ""),
        "content": content,
    }


class AsrPipeline:
    def __init__(self, *, config: AsrProviderConfig, recorder: RunRecorder) -> None:
        self.config = config
        self.recorder = recorder
        self._providers: dict[str, AsrProviderAdapter] = {}
        self._lock = threading.RLock()

    def append_audio(self, chunk: StreamChunk) -> str | None:
        final_text: str | None = None
        provider_key = chunk.stream_id or chunk.session_id
        provider = self._provider_for(provider_key)
        try:
            for event in provider.append_audio(chunk):
                name = "input_transcript.done" if event.final else "input_transcript.delta"
                if event.text:
                    self.recorder.record_timeline_checkpoint(
                        chunk.session_id,
                        checkpoint="text.timeline.asr.first_char",
                        user_id=chunk.user_id,
                        stream_id=chunk.stream_id,
                        fields={
                            "provider": provider.provider_name,
                            "model": provider.model,
                            "text_preview": event.text[:40],
                            "text_chars": len(event.text),
                        },
                    )
                self.recorder.record_agent_event(
                    chunk.session_id,
                    {
                        "event": name,
                        "text": event.text,
                        "provider": provider.provider_name,
                        "model": provider.model,
                        "stream_id": chunk.stream_id,
                    },
                )
                if event.final:
                    self.recorder.record_timeline_checkpoint(
                        chunk.session_id,
                        checkpoint="text.timeline.asr.done",
                        user_id=chunk.user_id,
                        stream_id=chunk.stream_id,
                        fields={
                            "provider": provider.provider_name,
                            "model": provider.model,
                            "text_preview": event.text[:80],
                            "text_chars": len(event.text),
                        },
                    )
                    final_text = event.text
        finally:
            if chunk.final:
                self._close_provider(provider_key)
        return final_text

    def cancel(self) -> None:
        with self._lock:
            providers = list(self._providers.values())
            self._providers.clear()
        for provider in providers:
            provider.cancel()

    def _provider_for(self, provider_key: str) -> AsrProviderAdapter:
        """按输入流返回独立 ASR provider。

        主要逻辑：真实 realtime ASR 在 final 后会关闭底层会话；浏览器眼镜每段录音
        会打开新的 `sensor.mic` stream，因此这里按 stream_id 隔离 provider，避免
        第二段音频复用已关闭的识别会话。
        参数：`provider_key` 通常为输入 stream_id。
        返回值：可继续接收该输入流音频的 ASR provider。
        异常情况：provider 构造失败时由 `build_asr_provider` 按配置降级或抛出。
        """

        with self._lock:
            provider = self._providers.get(provider_key)
            if provider is not None:
                return provider
            provider, downgrade_reason = build_asr_provider(self.config)
            if downgrade_reason:
                self._record_degradation(downgrade_reason)
            self._providers[provider_key] = provider
            return provider

    def _close_provider(self, provider_key: str) -> None:
        """关闭并移除一条输入流对应的 ASR provider。"""

        with self._lock:
            provider = self._providers.pop(provider_key, None)
        if provider is not None:
            provider.cancel()

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


class TextResponseGate:
    """Text 模型输出门控器。

    主要功能：在一次 LLM streaming 调用内接收文本 delta 后立即释放给 TTS；
    如果随后出现工具调用，已经释放的自然语言提示也会保留到最终 assistant 消息，
    避免把模型对用户的合理说明误判为废话。
    主要方法：`buffer()` 缓冲文本，`release()` 释放给输出链路，`discard()` 丢弃。
    主要属性：`emitted_text` 表示本次调用是否已有文本真正进入输出链路。
    """

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        emit,
        recorder: RunRecorder,
    ) -> None:
        self.user_id = user_id
        self.session_id = session_id
        self.emit = emit
        self.recorder = recorder
        self._buffer: list[str] = []
        self.emitted_text = False

    def buffer(self, text: str) -> None:
        """缓冲一段模型文本。

        主要逻辑：空文本忽略；非空文本先进入内存缓冲，随后由调用方立即释放给
        TTS。保留缓冲层是为了统一记录、工具调用前 flush 和错误恢复语义。
        参数：`text` 为模型 streaming delta。
        返回值：无。
        异常情况：无。
        """

        if not text:
            return
        self._buffer.append(text)
        self._record(
            "text.response_gate.buffered",
            delta_chars=len(text),
            buffered_chars=sum(len(item) for item in self._buffer),
            buffered_parts=len(self._buffer),
        )

    @property
    def has_buffered_text(self) -> bool:
        """返回当前是否有尚未释放的模型文本。"""

        return bool(self._buffer)

    def release(self) -> tuple[list[str], bool]:
        """释放缓冲文本。

        主要逻辑：按原 delta 顺序写入输出链路；如果输出失败，仍返回完整文本用于
        消息历史，但后续 delta 不再继续尝试输出，保持旧 TextAgentCore 的恢复语义。
        参数：无。
        返回值：`(released_texts, output_ok)`。
        异常情况：输出异常由 `emit` 包装为 False，本函数不抛出。
        """

        return self._release(reason="explicit_release")

    def release_ready(self, *, reason: str) -> tuple[list[str], bool]:
        """在文本 delta 到达后立即释放。

        主要逻辑：Text 链路不能等自然停顿或完整回复结束后才送 TTS；每个模型
        text delta 到达后都应尽快进入 TTS，让端侧尽早收到 speaker 音频。
        参数：`reason` 标识触发释放的原因。
        返回值：`(released_texts, output_ok)`。
        异常情况：输出异常由 `_release()` 内部记录和降级。
        """

        if not self._buffer:
            return [], True
        return self._release(reason=reason)

    def _release(self, *, reason: str) -> tuple[list[str], bool]:
        """执行实际释放并记录原因。"""

        texts = list(self._buffer)
        self._buffer.clear()
        output_ok = True
        for text in texts:
            if output_ok:
                if not self.emit(text):
                    output_ok = False
                else:
                    self.emitted_text = True
        if texts:
            self._record(
                "text.response_gate.released",
                released_chars=sum(len(item) for item in texts),
                released_parts=len(texts),
                output_ok=output_ok,
                reason=reason,
            )
        return texts, output_ok

    def discard(self, *, reason: str) -> list[str]:
        """丢弃缓冲文本。

        主要逻辑：保留给后续更细粒度策略使用；默认工具调用路径不丢弃文本，避免
        误删模型对用户的合理提示。
        参数：`reason` 为丢弃原因。
        返回值：被丢弃的文本片段，便于测试和诊断。
        异常情况：无。
        """

        texts = list(self._buffer)
        self._buffer.clear()
        if texts:
            self._record(
                "text.response_gate.discarded",
                reason=reason,
                discarded_chars=sum(len(item) for item in texts),
                discarded_parts=len(texts),
                discarded_preview="".join(texts)[:80],
            )
        return texts

    def _record(self, event: str, **payload) -> None:
        """记录门控事件。"""

        self.recorder.record_agent_event(
            self.session_id,
            {"event": event, "user_id": self.user_id, **payload},
        )


class TextAgentCore:
    RECOVERABLE_ERROR_MESSAGE = DEFAULT_RECOVERABLE_ERROR_MESSAGE

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
        multimodal_policy: MultimodalMessagePolicy | None = None,
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
        self._session_by_user: dict[str, str] = {}
        self._event_buffer = AgentEventBuffer()
        self.tool_gateway = tool_gateway
        self.memory_service = memory_service
        self.max_context_messages = max(1, int(max_context_messages or 30))
        self.context_compiler = ContextCompiler()
        self.message_manager = ModelMessageManager(multimodal_policy)
        self._state_by_user: dict[str, str] = {}
        self._interruption_reason_by_user: dict[str, str] = {}

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
        self._session_by_user[user_id] = session_id

    def append_audio_event(self, chunk: StreamChunk) -> None:
        self._set_turn_state(chunk.user_id, chunk.session_id, "transcribing", reason="audio_final_check")
        transcript = self.asr_pipeline.append_audio(chunk)
        if transcript is None:
            return
        self._set_turn_state(chunk.user_id, chunk.session_id, "thinking", reason="transcript_final")
        turn_key = self._turn_key(chunk=chunk, transcript=transcript)
        if turn_key in self._responded_input_streams:
            self._record_duplicate_turn(chunk=chunk, transcript=transcript, turn_key=turn_key, reason="duplicate_turn")
            self._set_turn_state(chunk.user_id, chunk.session_id, "completed", reason="duplicate_turn")
            return
        self._responded_input_streams.add(turn_key)
        self._cancelled_users.discard(chunk.user_id)
        self._interruption_reason_by_user.pop(chunk.user_id, None)
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
        response_error: Exception | None = None
        try:
            assistant_text = self._run_tool_loop(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                transcript=transcript,
            )
        except Exception as exc:
            response_error = exc
            assistant_text = self.RECOVERABLE_ERROR_MESSAGE
            self._handle_response_error(chunk=chunk, error=exc, fallback_text=assistant_text)
            self._emit_output_best_effort(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                stream_id=chunk.stream_id,
                stream_type=chunk.stream_type,
                text=assistant_text,
                final=False,
                context="fallback_text",
            )
        self._emit_output_best_effort(
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_id=chunk.stream_id,
            stream_type=chunk.stream_type,
            text="",
            final=True,
            context="final_flush",
        )
        interrupted_reason = self._interruption_reason_by_user.pop(chunk.user_id, None)
        self.control_service.append_message(
            chunk.user_id,
            {
                "session_id": chunk.session_id,
                "role": "assistant",
                "content": assistant_text,
                "event": "assistant_text.done",
                "error": str(response_error) if response_error is not None else None,
                "interrupted": bool(interrupted_reason),
                "interrupted_reason": interrupted_reason,
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
            recovered_from_error=response_error is not None,
            interrupted=bool(interrupted_reason),
            interrupted_reason=interrupted_reason,
        )
        self._set_turn_state(
            chunk.user_id,
            chunk.session_id,
            "interrupted" if interrupted_reason else ("failed" if response_error is not None else "completed"),
            reason=interrupted_reason or ("response_error" if response_error is not None else "response_done"),
        )

    @staticmethod
    def _turn_key(*, chunk: StreamChunk, transcript: str) -> str:
        """生成文本 Agent 的输入轮次去重 key。

        主要逻辑：browser-glass 离线音频会在同一个 `sensor.mic` stream 中连续提交
        多个 final chunk；如果只按 stream_id 去重，第二段音频会被静默跳过。
        因此 key 同时包含 stream_id、final chunk seq 和转写文本，表达的是一次
        用户输入 turn，而不是整个底层 stream。
        参数：`chunk` 为触发 final transcript 的音频分片；`transcript` 为最终转写。
        返回值：稳定字符串 key。
        异常情况：无。
        """

        stream_id = chunk.stream_id or chunk.session_id
        return f"{stream_id}:{chunk.seq}:{transcript.strip()}"

    def _record_duplicate_turn(self, *, chunk: StreamChunk, transcript: str, turn_key: str, reason: str) -> None:
        """记录被去重跳过的输入 turn。"""

        self._record_event(
            "response.skipped",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            agent_core="TextAgentCore",
            reason=reason,
            turn_key=turn_key,
            stream_id=chunk.stream_id,
            seq=chunk.seq,
            transcript=transcript,
        )

    def _handle_response_error(self, *, chunk: StreamChunk, error: Exception, fallback_text: str) -> None:
        """处理一轮文本响应中的可恢复异常。

        主要逻辑：模型 provider 或工具循环异常只影响当前轮响应，不继续抛到
        WebSocket stream 层；同时写入系统错误、Agent 失败事件并下发控制事件，
        让端侧和 runs 产物都能看到失败原因。
        参数：`chunk` 为触发本轮响应的音频分片；`error` 为原始异常；
        `fallback_text` 为用户可听的兜底反馈。
        返回值：无。
        异常情况：记录或下发失败时不再抛出，避免掩盖原始恢复流程。
        """

        record_agent_recovery_error(
            recorder=self.recorder,
            event_buffer=self._event_buffer,
            control_service=self.control_service,
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_id=chunk.stream_id,
            stream_type=chunk.stream_type,
            component="TextAgentCore",
            error=error,
            agent_event="response.failed",
            recoverable=True,
            fallback_text=fallback_text,
        )

    def _emit_output_best_effort(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str | None,
        stream_type: str | None,
        text: str,
        final: bool,
        context: str,
    ) -> bool:
        """尽力向输出链路发送文本 delta，输出失败时只记录不抛出。

        主要逻辑：异常恢复过程中不能再依赖同一个可能损坏的 TTS/output 状态；
        如果兜底提示或 final flush 失败，只写入统一错误面，当前轮仍继续收尾。
        参数：`user_id/session_id` 定位会话；`stream_id/stream_type` 保留触发输入；
        `text/final` 为要发送的文本 delta；`context` 标识输出阶段。
        返回值：发送成功返回 True，失败返回 False。
        异常情况：本函数不向外抛出输出异常。
        """

        try:
            self.output_adapter.emit_text_delta(user_id=user_id, session_id=session_id, text=text, final=final)
            return True
        except Exception as exc:
            record_agent_recovery_error(
                recorder=self.recorder,
                event_buffer=self._event_buffer,
                control_service=self.control_service,
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                stream_type=stream_type,
                component="TextOutputAdapter",
                error=exc,
                agent_event="output.failed",
                recoverable=True,
                fallback_text=self.RECOVERABLE_ERROR_MESSAGE,
                record={"output_context": context, "final": final},
            )
            return False

    def _run_tool_loop(self, *, user_id: str, session_id: str, transcript: str) -> str:
        """运行 Text Agent 工具循环。

        主要逻辑：支持 provider 通过 `stream_messages(messages, tools)` 返回
        `tool_call` 事件；每次 ToolResult 会写入消息历史，然后继续请求模型生成后续回复。
        参数：`user_id/session_id/transcript` 定位当前轮次。
        返回值：最终助手文本。
        异常情况：ToolGateway 会把工具异常转换为 ToolResult，本函数不抛业务异常。
        """

        assistant_parts: list[str] = []
        context = self.context_compiler.compile(
            ContextCompileRequest(
                mode="text",
                provider=getattr(self.text_model, "provider_name", "unknown"),
                model=getattr(self.text_model, "model", "unknown"),
                user_id=user_id,
                session_id=session_id,
                base_instructions=str(getattr(self.text_model, "prompt", TEXT_AGENT_SYSTEM_PROMPT)),
                current_input={"type": "text", "transcript": transcript},
                include_tools=True,
                reason="text_agent_turn",
                memory_service=self.memory_service,
                control_service=self.control_service,
                tool_gateway=self.tool_gateway,
                max_context_messages=self.max_context_messages,
            )
        )
        messages: list[dict[str, Any]] = list(context.messages)
        tools = list(context.tools)
        prompt = context.instructions
        previous_prompt = getattr(self.text_model, "prompt", None)
        if previous_prompt is not None:
            setattr(self.text_model, "prompt", prompt)
        record_context_events(recorder=self.recorder, session_id=session_id, context=context)
        dynamic_context_sources: list[dict[str, Any]] = []

        def record_current_model_request(*, reason: str) -> None:
            self.recorder.record_model_request(
                session_id,
                {
                    "provider": context.provider,
                    "model": context.model,
                    "runner": "agent_core_text",
                    "user_id": user_id,
                    "session_id": session_id,
                    "prompt": prompt,
                    "messages": [{"role": "system", "content": prompt}, *self._model_request_messages(messages)],
                    "tools": tools,
                    "tool_count": len(tools),
                    "prompts": context.prompt_records(),
                    "context_sources": [*context.source_records(), *dynamic_context_sources],
                    "warnings": context.warnings,
                    "truncations": context.truncations,
                    "notifications": context.notifications,
                    "provider_request_options": (
                        self.text_model.request_options_snapshot()
                        if hasattr(self.text_model, "request_options_snapshot")
                        else {}
                    ),
                    "context_metadata": {**context.metadata, "request_reason": reason},
                },
            )

        try:
            model_text_delta_count = 0
            model_text_chars = 0
            for iteration in range(4):
                record_current_model_request(reason=f"text_agent_turn_iteration_{iteration + 1}")
                tool_calls: list[dict[str, Any]] = []
                model_output_started = False
                gate = TextResponseGate(
                    user_id=user_id,
                    session_id=session_id,
                    recorder=self.recorder,
                    emit=lambda text: self._emit_assistant_text_delta(
                        user_id=user_id,
                        session_id=session_id,
                        text=text,
                    ),
                )
                for item in self._stream_model(messages=messages, transcript=transcript, tools=tools):
                    if user_id in self._cancelled_users:
                        self._record_event(
                            "response.interrupted",
                            user_id=user_id,
                            session_id=session_id,
                            reason=self._interruption_reason_by_user.get(user_id, "user_interrupt"),
                            released_chars=sum(len(part) for part in assistant_parts),
                            buffered_chars=sum(len(part) for part in getattr(gate, "_buffer", [])),
                        )
                        return "".join(assistant_parts)
                    if isinstance(item, dict) and item.get("type") == "tool_call":
                        if not model_output_started:
                            model_output_started = True
                        released_texts, _output_ok = gate.release()
                        assistant_parts.extend(released_texts)
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
                    model_text_delta_count += 1
                    model_text_chars += len(text_delta)
                    self.recorder.record_timeline_checkpoint(
                        session_id,
                        checkpoint="text.timeline.llm.first_token",
                        user_id=user_id,
                        fields={
                            "provider": getattr(self.text_model, "provider_name", "unknown"),
                            "model": getattr(self.text_model, "model", "unknown"),
                            "text_preview": text_delta[:40],
                            "delta_chars": len(text_delta),
                        },
                    )
                    if not model_output_started:
                        model_output_started = True
                    gate.buffer(text_delta)
                    released_texts, _output_ok = gate.release_ready(reason="text_delta_realtime")
                    assistant_parts.extend(released_texts)
                if not tool_calls or self.tool_gateway is None:
                    released_texts, _output_ok = gate.release()
                    assistant_parts.extend(released_texts)
                    break
                released_texts, _output_ok = gate.release()
                assistant_parts.extend(released_texts)
                provider_tool_call_message = _provider_tool_call_message(tool_calls)
                messages.append(provider_tool_call_message)
                self.control_service.append_message(
                    user_id,
                    {
                        "session_id": session_id,
                        "role": "assistant",
                        "content": "",
                        "tool_calls": _audit_tool_calls(tool_calls),
                        "event": "assistant_tool_call.done",
                        "source": "text_agent",
                    },
                )
                for tool_call in tool_calls:
                    if not gate.emitted_text:
                        self.tool_gateway.emit_progress_once(
                            name=str(tool_call.get("name") or ""),
                            user_id=user_id,
                            session_id=session_id,
                            output_service=self.output_service,
                        )
                    self._set_turn_state(user_id, session_id, "tool_running", reason=str(tool_call.get("name") or "tool_call"))
                    result = self._call_tool(
                        name=str(tool_call.get("name") or ""),
                        user_id=user_id,
                        session_id=session_id,
                        input_data=dict(tool_call.get("arguments") or {}),
                    )
                    self._set_turn_state(user_id, session_id, "thinking", reason="tool_result_returned")
                    result_dict = self._tool_result_to_dict(result)
                    self.recorder.record_agent_event(
                        session_id,
                        {
                            "event": "context.source.added",
                            "source_id": f"tool_result:{tool_call.get('name') or ''}",
                            "source_kind": "tool",
                            "source_name": f"tool_result:{tool_call.get('name') or ''}",
                            "included": True,
                            "reason": "text_tool_loop_provider_message",
                        },
                    )
                    messages.append(_provider_tool_result_message(tool_call=tool_call, result=result_dict))
                    update = self.message_manager.append_tool_result_followup(
                        messages=messages,
                        tool_call=tool_call,
                        tool_result=result_dict,
                        user_id=user_id,
                        session_id=session_id,
                    )
                    messages.extend(update.messages)
                    dynamic_context_sources.extend(update.source_records)
                    for event in update.events:
                        self.recorder.record_agent_event(session_id, event)
                    self.control_service.append_message(
                        user_id,
                        {
                            "session_id": session_id,
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "name": tool_call.get("name"),
                            "content": result_dict,
                            "event": "tool_result.done",
                            "source": "text_agent",
                        },
                    )
        finally:
            if previous_prompt is not None:
                setattr(self.text_model, "prompt", previous_prompt)
        self.recorder.record_timeline_checkpoint(
            session_id,
            checkpoint="text.timeline.llm.done",
            user_id=user_id,
            fields={
                "provider": getattr(self.text_model, "provider_name", "unknown"),
                "model": getattr(self.text_model, "model", "unknown"),
                "text_delta_count": model_text_delta_count,
                "text_chars": model_text_chars,
            },
        )
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

    def _build_prompt(self, *, user_id: str, session_id: str) -> str:
        """构造当前轮文本模型提示词。

        主要逻辑：在静态提示词后追加长期记忆和更早历史摘要，避免把已压缩的原始
        对话重复放回 messages。
        参数：`user_id` 为当前用户编号，`session_id` 为设备级会话编号。
        返回值：发送给文本模型的提示词。
        异常情况：memory 或历史摘要读取失败时跳过对应片段。
        """

        parts = [str(getattr(self.text_model, "prompt", TEXT_AGENT_SYSTEM_PROMPT))]
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

    def _emit_assistant_text_delta(self, *, user_id: str, session_id: str, text: str) -> bool:
        """释放一段助手文本并同步 Text turn 状态。

        主要逻辑：文本真正进入输出链路前把状态切到 speaking；输出异常仍由
        `_emit_output_best_effort()` 转为可恢复事件。
        参数：`user_id/session_id/text` 定位本轮输出。
        返回值：输出成功返回 True。
        异常情况：不向外抛出输出异常。
        """

        self._set_turn_state(user_id, session_id, "speaking", reason="assistant_text_released")
        return self._emit_output_best_effort(
            user_id=user_id,
            session_id=session_id,
            stream_id=None,
            stream_type=None,
            text=text,
            final=False,
            context="assistant_text_delta",
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
    def _model_request_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """返回适合写入 `model-request.json` 的消息快照。

        主要逻辑：真实 provider 请求使用完整 data URL；运行产物只保留 content block
        类型和 data URL 长度，避免把 base64 大字段写入调试文件。
        参数：`messages` 为即将传给 provider 的消息。
        返回值：可 JSON 序列化的脱敏消息列表。
        异常情况：无。
        """

        return [TextAgentCore._redact_message_for_record(message) for message in messages]

    @staticmethod
    def _redact_message_for_record(message: dict[str, Any]) -> dict[str, Any]:
        record = dict(message)
        content = record.get("content")
        if isinstance(content, list):
            record["content"] = [TextAgentCore._redact_content_block(item) for item in content]
        return record

    @staticmethod
    def _redact_content_block(block: Any) -> Any:
        if not isinstance(block, dict):
            return block
        item = dict(block)
        for key in ("image_url", "video_url"):
            nested = item.get(key)
            if isinstance(nested, dict):
                nested_copy = dict(nested)
                url = str(nested_copy.get("url") or "")
                if url.startswith("data:"):
                    nested_copy["url"] = f"{url[:32]}...<redacted:{len(url)} chars>"
                item[key] = nested_copy
        return item

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
        self._interruption_reason_by_user[user_id] = reason
        session_id = self._session_by_user.get(user_id, "")
        self.asr_pipeline.cancel()
        self.text_model.cancel()
        if session_id:
            self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)
        self.recorder.record_agent_event(
            session_id or "interruptions",
            {"event": "agent.response.cancelled", "user_id": user_id, "reason": reason},
        )
        self._record_event("response.cancelled", user_id=user_id, session_id=session_id, reason=reason)
        if session_id:
            self._set_turn_state(user_id, session_id, "interrupted", reason=reason)

    def close(self, user_id: str, *, reason: str) -> None:
        """关闭文本 Agent 会话。

        主要逻辑：取消 ASR 和文本模型，清理当前用户取消标记，并记录统一关闭事件。
        参数：`user_id` 为用户标识；`reason` 为关闭原因。
        返回值：无。
        异常情况：provider cancel 异常由 provider 自身处理。
        """

        self._cancelled_users.discard(user_id)
        session_id = self._session_by_user.pop(user_id, None)
        self.asr_pipeline.cancel()
        self.text_model.cancel()
        if session_id and hasattr(self.output_service, "close_text_session"):
            self.output_service.close_text_session(session_id, reason=reason)
        self._record_event("session.closed", user_id=user_id, session_id=session_id, reason=reason)

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

    def _set_turn_state(self, user_id: str, session_id: str, state: str, *, reason: str) -> None:
        """记录 Text 链路状态机变化。

        主要逻辑：只记录状态变化，避免同一状态重复刷屏；状态同时进入统一
        Agent event buffer 和 runs，便于真实端侧回放后按时间线排查。
        参数：`user_id/session_id` 定位会话；`state` 是新状态；`reason` 是触发原因。
        返回值：无。
        异常情况：无。
        """

        key = user_id or session_id
        previous = self._state_by_user.get(key, "listening")
        if previous == state:
            return
        self._state_by_user[key] = state
        self._record_event(
            "agent.turn_state.changed",
            user_id=user_id,
            session_id=session_id,
            agent_core="TextAgentCore",
            modality="text",
            previous_state=previous,
            state=state,
            reason=reason,
        )
