from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, is_dataclass
from typing import Any, Callable

from realtime_agent.control import ControlService
from realtime_agent.observability import RunRecorder
from realtime_agent.output import AssistantTextDelta, OutputService
from realtime_agent.protocol import SERVER_PRODUCER_ID, Event, StreamChunk, new_id
from realtime_agent.conversation.context import ContextCompileRequest, ContextCompiler, record_context_events
from realtime_agent.conversation.context.models import normalize_history_message
from realtime_agent.conversation.core.base import AgentCoreEvent, AgentEventBuffer
from realtime_agent.conversation.multimodal import ModelMessageManager, MultimodalMessagePolicy
from realtime_agent.conversation.providers.model_adapters import (
    AsrProviderAdapter,
    AsrProviderConfig,
    VISION_AGENT_SYSTEM_PROMPT,
    VisionModelProviderConfig,
    build_asr_provider,
    build_vision_model,
)
from realtime_agent.conversation.core.recovery import DEFAULT_RECOVERABLE_ERROR_MESSAGE, record_agent_recovery_error
from realtime_agent.conversation.input.visual_appender import VisualAppendContext, VlVisualAppender
from realtime_agent.asset import AssetService
from realtime_agent.errors import ErrorCode
from realtime_agent.tools import ToolError, ToolGateway, ToolResult



def _provider_tool_call_message(tool_calls: list[dict[str, Any]]) -> dict[str, Any]:
    """把 SDK 内部工具调用记录转换为模型 provider 可消费的 assistant 消息。

    主要逻辑：VisionModelAdapter 对外统一输出扁平的 `tool_call` 字典，便于
    ToolGateway 调用；Chat Completions 兼容接口要求回填到历史消息时使用
    `tool_calls[].function` 嵌套结构，且 arguments 必须是 JSON 字符串。
    参数：`tool_calls` 为本轮模型输出的内部工具调用列表。
    返回值：可直接传给视觉语言模型 provider 的 assistant 消息。
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
    def __init__(
        self,
        *,
        config: AsrProviderConfig,
        recorder: RunRecorder,
        on_transcript_event: Callable[[StreamChunk, Any], None] | None = None,
    ) -> None:
        self.config = config
        self.recorder = recorder
        self.on_transcript_event = on_transcript_event
        self._providers: dict[str, AsrProviderAdapter] = {}
        self._lock = threading.RLock()

    def append_audio(self, chunk: StreamChunk) -> str | None:
        final_text: str | None = None
        provider_key = chunk.stream_id or chunk.session_id
        provider = self._provider_for(provider_key)
        try:
            for event in provider.append_audio(chunk):
                name = "input_transcript.done" if event.final else "input_transcript.delta"
                event_payload = {
                    "event": name,
                    "text": event.text,
                    "provider": provider.provider_name,
                    "model": provider.model,
                    "stream_id": chunk.stream_id,
                }
                for attr in (
                    "sentence_id",
                    "sentence_begin",
                    "sentence_end",
                    "begin_time_ms",
                    "end_time_ms",
                    "words",
                ):
                    value = getattr(event, attr, None)
                    if value not in (None, False, []):
                        event_payload[attr] = value
                if event.text:
                    self.recorder.record_timeline_checkpoint(
                        chunk.session_id,
                        checkpoint="vision.timeline.asr.first_char",
                        user_id=chunk.user_id,
                        stream_id=chunk.stream_id,
                        fields={
                            "provider": provider.provider_name,
                            "model": provider.model,
                            "text_preview": event.text[:40],
                            "text_chars": len(event.text),
                        },
                    )
                self.recorder.record_agent_event(chunk.session_id, event_payload)
                if self.on_transcript_event is not None:
                    self.on_transcript_event(chunk, event)
                if event.final:
                    self.recorder.record_timeline_checkpoint(
                        chunk.session_id,
                        checkpoint="vision.timeline.asr.done",
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

    def commit_audio(self, chunk: StreamChunk) -> str | None:
        """提交当前连续麦克风输入，取回 ASR 最终文本。

        主要逻辑：连续麦克风链路不会用 `StreamChunk.final` 表达一句话结束；
        server VAD 的 speech_stopped 才是本地 turn boundary。这里构造一个空
        final chunk 交给 ASR provider，让 mock ASR 和真实实时 ASR 都有统一的
        commit 入口。
        """

        final_chunk = StreamChunk(
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_id=chunk.stream_id,
            stream_type=chunk.stream_type,
            seq=chunk.seq,
            payload=b"",
            codec=chunk.codec,
            sample_rate=chunk.sample_rate,
            channels=chunk.channels,
            duration_ms=0,
            final=True,
            metadata=dict(chunk.metadata or {}),
        )
        return self.append_audio(final_chunk)

    def prepare_provider(self, *, stream_id: str, session_id: str | None = None) -> None:
        """提前建立指定麦克风输入流的 ASR provider。

        主要逻辑：Vision realtime 标准要求上行音频长连接建立时就连接 realtime ASR，
        避免第一帧音频到达后才承担 provider 建连延迟。`session_id` 只用于观测事件。
        参数：`stream_id` 为上行麦克风 stream；`session_id` 为当前音频会话。
        返回值：无。
        异常情况：provider 创建失败时沿用 `_provider_for()` 的降级或抛错策略。
        """

        if not stream_id:
            return
        self._provider_for(stream_id)
        self.recorder.record_agent_event(
            session_id or stream_id,
            {"event": "vision.asr_provider.prepared", "stream_id": stream_id, "provider": self.config.provider, "model": self.config.model},
        )

    def close_provider(self, *, stream_id: str) -> None:
        """关闭指定麦克风输入流的 ASR provider。"""

        if stream_id:
            self._close_provider(stream_id)

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


class VisionOutputAdapter:
    def __init__(self, *, output_service: OutputService, recorder: RunRecorder) -> None:
        self.output_service = output_service
        self.recorder = recorder

    def emit_vision_delta(
        self,
        *,
        user_id: str,
        session_id: str,
        text: str,
        final: bool = False,
        generation_id: int | None = None,
    ) -> None:
        self.recorder.record_agent_event(
            session_id,
            {"event": "assistant_text.delta", "text": text, "final": final},
        )
        self.output_service.on_assistant_vision_delta(
            AssistantTextDelta(
                user_id=user_id,
                session_id=session_id,
                text=text,
                final=final,
                generation_id=generation_id,
            )
        )


class TextResponseGate:
    """Vision 模型输出门控器。

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
            "vision.response_gate.buffered",
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
        消息历史，但后续 delta 不再继续尝试输出，保持旧 VisionRealtimeAgentCore 的恢复语义。
        参数：无。
        返回值：`(released_texts, output_ok)`。
        异常情况：输出异常由 `emit` 包装为 False，本函数不抛出。
        """

        return self._release(reason="explicit_release")

    def release_ready(self, *, reason: str) -> tuple[list[str], bool]:
        """在文本 delta 到达后立即释放。

        主要逻辑：Vision 链路不能等自然停顿或完整回复结束后才送 TTS；每个模型
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
                "vision.response_gate.released",
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
                "vision.response_gate.discarded",
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


class VisionRealtimeAgentCore:
    RECOVERABLE_ERROR_MESSAGE = DEFAULT_RECOVERABLE_ERROR_MESSAGE

    def __init__(
        self,
        *,
        control_service: ControlService,
        output_service: OutputService,
        recorder: RunRecorder,
        asr_config: AsrProviderConfig | None = None,
        vision_model_config: VisionModelProviderConfig | None = None,
        tool_gateway: ToolGateway | None = None,
        asset_service: AssetService | None = None,
        memory_service: Any = None,
        max_context_messages: int = 30,
        multimodal_policy: MultimodalMessagePolicy | None = None,
        on_user_activity: Callable[[str, str], None] | None = None,
        realtime_video_enabled: bool = False,
        visual_frame_interval_seconds: float = 1.0,
        visual_frame_timeout_seconds: float = 1.5,
        visual_frame_ttl_seconds: float = 5.0,
        visual_max_frames_per_turn: int = 8,
        visual_direction: str = "front",
    ) -> None:
        self.control_service = control_service
        self.output_service = output_service
        self.output_adapter = VisionOutputAdapter(output_service=output_service, recorder=recorder)
        self.recorder = recorder
        self.asr_pipeline = AsrPipeline(
            config=asr_config or AsrProviderConfig(),
            recorder=recorder,
            on_transcript_event=self._handle_asr_transcript_event,
        )
        self.vision_model, downgrade_reason = build_vision_model(vision_model_config or VisionModelProviderConfig())
        if downgrade_reason:
            self.recorder.record_system_event(
                {"event": "system.degradation.raised", "component": "VisionModelAdapter", "reason": downgrade_reason}
            )
        self._responded_input_streams: set[str] = set()
        self._cancelled_users: set[str] = set()
        self._session_by_user: dict[str, str] = {}
        self._event_buffer = AgentEventBuffer()
        self.tool_gateway = tool_gateway
        self.asset_service = asset_service
        self.memory_service = memory_service
        self.max_context_messages = max(1, int(max_context_messages or 30))
        self.context_compiler = ContextCompiler()
        self.message_manager = ModelMessageManager(multimodal_policy, asset_service=asset_service)
        self.visual_appender = VlVisualAppender(self.message_manager)
        self._state_by_user: dict[str, str] = {}
        self._interruption_reason_by_user: dict[str, str] = {}
        self._assistant_output_guard_by_user: dict[str, tuple[int, int]] = {}
        self._response_generation_by_user: dict[str, int] = {}
        self._interrupted_generation_reason_by_user: dict[str, dict[int, str]] = {}
        self._assistant_parts_by_generation: dict[str, dict[int, list[str]]] = {}
        self._finalized_generation_reason_by_user: dict[str, dict[int, str]] = {}
        self._generation_lock = threading.RLock()
        self._asr_started_sentence_keys: set[str] = set()
        self._asr_stopped_sentence_keys: set[str] = set()
        self._on_user_activity = on_user_activity
        self.realtime_video_enabled = bool(realtime_video_enabled)
        self.visual_frame_interval_seconds = float(visual_frame_interval_seconds or 0)
        self.visual_frame_timeout_seconds = float(visual_frame_timeout_seconds or 0)
        self.visual_frame_ttl_seconds = float(visual_frame_ttl_seconds or 0)
        self.visual_max_frames_per_turn = max(0, int(visual_max_frames_per_turn or 0))
        self.visual_direction = str(visual_direction or "front").strip() or "front"
        self._audio_stream_by_session: dict[str, str] = {}
        self._latest_audio_chunk_by_session: dict[str, StreamChunk] = {}
        self._closed_audio_streams_by_session: dict[str, set[str]] = {}
        self._visual_sampler_stop_by_session: dict[str, threading.Event] = {}
        self._visual_sampler_threads_by_session: dict[str, threading.Thread] = {}
        self._visual_sampler_frame_count_by_session: dict[str, int] = {}

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定 VisionRealtimeAgentCore 使用的 ToolGateway。

        主要逻辑：App 在完成工具发现、内置工具注册和策略配置后注入网关；
        Agent Core 只依赖网关，不直接 import 业务 Tool。
        参数：`tool_gateway` 为 SDK 统一工具网关。
        返回值：无。
        异常情况：无。
        """

        self.tool_gateway = tool_gateway

    def open(self, user_id: str, session_id: str) -> None:
        """打开文本 Agent 会话。

        主要逻辑：Vision 链路不需要提前连接模型 provider，只记录统一会话事件。
        参数：`user_id` 为用户标识，`session_id` 为当前会话。
        返回值：无。
        异常情况：无。
        """

        self._record_event("session.opened", user_id=user_id, session_id=session_id, agent_core="VisionRealtimeAgentCore")
        self._session_by_user[user_id] = session_id

    def bind_user_activity_callback(self, callback: Callable[[str, str], None]) -> None:
        """绑定用户有效语音活动回调。

        主要逻辑：连续麦克风长连接会持续发送静音音频，不能用每个音频 chunk 刷新
        对话空闲时间；只有 ASR 句边界或最终用户输入才算有效用户活动。
        """

        self._on_user_activity = callback

    def on_audio_input_opened(self, *, user_id: str, session_id: str, stream_id: str) -> None:
        """通知 Vision 链路上行麦克风 stream 已建立。"""

        self._audio_stream_by_session[session_id] = stream_id
        self._closed_audio_streams_by_session.get(session_id, set()).discard(stream_id)
        self.asr_pipeline.prepare_provider(stream_id=stream_id, session_id=session_id)

    def on_audio_input_closed(self, *, user_id: str, session_id: str, stream_id: str, reason: str) -> None:
        """通知 Vision 链路上行麦克风 stream 已关闭。"""

        self._closed_audio_streams_by_session.setdefault(session_id, set()).add(stream_id)
        self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason=f"audio_stream_closed:{reason}")
        self.asr_pipeline.close_provider(stream_id=stream_id)
        self._record_event(
            "audio_input.closed",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason=reason,
        )

    def append_audio_event(self, chunk: StreamChunk) -> None:
        self._audio_stream_by_session[chunk.session_id] = chunk.stream_id
        self._latest_audio_chunk_by_session[chunk.session_id] = chunk
        self._set_turn_state(chunk.user_id, chunk.session_id, "transcribing", reason="audio_final_check")
        transcript = self.asr_pipeline.append_audio(chunk)
        if transcript is None:
            if chunk.final:
                self._stop_visual_sampler(
                    user_id=chunk.user_id,
                    session_id=chunk.session_id,
                    reason="audio_final_without_transcript",
                )
            return
        self._handle_final_transcript(chunk=chunk, transcript=transcript)

    def _handle_final_transcript(self, *, chunk: StreamChunk, transcript: str) -> None:
        """处理 ASR 最终文本并触发 Vision 回复。"""

        if self._should_ignore_transcript_as_echo(chunk=chunk, transcript=transcript):
            self._stop_visual_sampler(user_id=chunk.user_id, session_id=chunk.session_id, reason="echo_guard_ignored")
            self._set_turn_state(chunk.user_id, chunk.session_id, "completed", reason="echo_guard_ignored")
            return
        self._set_turn_state(chunk.user_id, chunk.session_id, "thinking", reason="transcript_final")
        self._mark_user_activity(chunk.user_id, chunk.session_id)
        turn_key = self._turn_key(chunk=chunk, transcript=transcript)
        if turn_key in self._responded_input_streams:
            self._record_duplicate_turn(chunk=chunk, transcript=transcript, turn_key=turn_key, reason="duplicate_turn")
            self._stop_visual_sampler(user_id=chunk.user_id, session_id=chunk.session_id, reason="duplicate_turn")
            self._set_turn_state(chunk.user_id, chunk.session_id, "completed", reason="duplicate_turn")
            return
        self._responded_input_streams.add(turn_key)
        generation = self._next_response_generation(chunk.user_id)
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
                payload={"agent_core": "VisionRealtimeAgentCore"},
            )
        )
        self._record_event(
            "response.started",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            agent_core="VisionRealtimeAgentCore",
        )
        self._stop_visual_sampler(user_id=chunk.user_id, session_id=chunk.session_id, reason="transcript_final", wait=True)
        self._maybe_capture_visual_frame_before_response(user_id=chunk.user_id, session_id=chunk.session_id)
        if not chunk.final:
            thread = threading.Thread(
                target=self._run_response_turn,
                kwargs={"chunk": chunk, "transcript": transcript, "generation": generation},
                name=f"text-response-{chunk.session_id}",
                daemon=True,
            )
            thread.start()
            return
        self._run_response_turn(chunk=chunk, transcript=transcript, generation=generation)

    def _run_response_turn(self, *, chunk: StreamChunk, transcript: str, generation: int) -> None:
        """执行 Vision 模型、工具和输出回复。

        主要逻辑：连续上行链路中，ASR 可能在非 final chunk 上给出最终文本；此时回复必须
        脱离 mic stream worker 后台执行，否则长文本/TTS 会阻塞后续麦克风 chunk、VAD 和
        打断处理。显式 final chunk 仍可同步调用本方法，保持离线回放和单元测试语义。
        参数：`chunk` 为触发本轮回复的音频片；`transcript` 为用户最终文本。
        返回值：无。
        异常情况：模型或输出异常会转成可恢复回复并写入 runs。
        """

        response_error: Exception | None = None
        try:
            assistant_text = self._run_tool_loop(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                transcript=transcript,
                generation=generation,
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
                generation=generation,
            )
        interrupted_reason = self._response_cancel_reason(chunk.user_id, generation)
        self._interrupted_generation_reason_by_user.get(chunk.user_id, {}).pop(generation, None)
        if self._response_generation_by_user.get(chunk.user_id) == generation:
            self._interruption_reason_by_user.pop(chunk.user_id, None)
        if interrupted_reason and assistant_text and not assistant_text.endswith("<用户打断>"):
            assistant_text = f"{assistant_text}<用户打断>"
        if interrupted_reason is None:
            self._emit_output_best_effort(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                stream_id=chunk.stream_id,
                stream_type=chunk.stream_type,
                text="",
                final=True,
                context="final_flush",
                generation=generation,
            )
        message_already_finalized = self._generation_finalized_reason(chunk.user_id, generation) is not None
        if not message_already_finalized:
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
                payload={"agent_core": "VisionRealtimeAgentCore"},
            )
        )
        self._record_event(
            "response.done",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            agent_core="VisionRealtimeAgentCore",
            assistant_text=assistant_text,
            recovered_from_error=response_error is not None,
            interrupted=bool(interrupted_reason),
            interrupted_reason=interrupted_reason,
            message_already_finalized=message_already_finalized,
        )
        self._cleanup_generation(chunk.user_id, generation)
        self._set_turn_state(
            chunk.user_id,
            chunk.session_id,
            "interrupted" if interrupted_reason else ("failed" if response_error is not None else "completed"),
            reason=interrupted_reason or ("response_error" if response_error is not None else "response_done"),
        )
        if self.asset_service is not None:
            self.asset_service.clear_turn_buffer(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                reason=interrupted_reason or ("response_error" if response_error is not None else "response_done"),
            )
        if not interrupted_reason:
            self._extend_assistant_output_guard(chunk.user_id, start_ms=None, tail_ms=1500)
        self._notify_turn_completed(chunk.user_id, chunk.session_id)

    def bind_follow_up_flush(self, callback: Callable[[str], None]) -> None:
        """绑定 turn 完成后的 late result flush 回调。"""

        self._follow_up_flush = callback

    def _notify_turn_completed(self, user_id: str, session_id: str) -> None:
        """turn 完成后触发排队 late result 的 flush。"""

        callback = getattr(self, "_follow_up_flush", None)
        if callback is None:
            return
        try:
            callback(user_id)
        except Exception:  # noqa: BLE001 - flush 异常不应影响回复主流程
            pass

    def _should_ignore_transcript_as_echo(self, *, chunk: StreamChunk, transcript: str) -> bool:
        """判断 ASR final 是否落在助手输出保护窗内。

        主要逻辑：Vision 链路当前仍在同一个 mic stream worker 中同步生成回复；助手播放期间
        的麦克风 chunk 可能排队到回复结束后才送入 ASR。若 chunk 时间戳落在助手输出开始
        到播放尾音保护窗之间，则把该 final 当作潜在回声丢弃，避免助手自问自答。
        参数：`chunk` 为触发 final 的音频片；`transcript` 为 ASR final 文本。
        返回值：需要忽略返回 True。
        异常情况：无。
        """

        guard = self._assistant_output_guard_by_user.get(chunk.user_id)
        if guard is None:
            return False
        if chunk.final:
            return False
        start_ms, until_ms = guard
        timestamp_ms = int(chunk.timestamp_ms or self._now_ms())
        if timestamp_ms < start_ms or timestamp_ms > until_ms:
            return False
        self._record_event(
            "input_transcript.ignored",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_id=chunk.stream_id,
            reason="assistant_output_echo_guard",
            transcript=transcript,
            chunk_timestamp_ms=timestamp_ms,
            guard_start_ms=start_ms,
            guard_until_ms=until_ms,
        )
        return True

    def _extend_assistant_output_guard(self, user_id: str, *, start_ms: int | None, tail_ms: int) -> None:
        """更新助手输出后的输入保护窗口。"""

        now_ms = self._now_ms()
        current = self._assistant_output_guard_by_user.get(user_id)
        if current and start_ms is not None:
            guard_start = min(current[0], start_ms)
        else:
            guard_start = start_ms if start_ms is not None else (current[0] if current else now_ms)
        guard_until = max(current[1] if current else 0, now_ms + max(0, int(tail_ms)))
        self._assistant_output_guard_by_user[user_id] = (guard_start, guard_until)

    @staticmethod
    def _now_ms() -> int:
        return int(time.time() * 1000)

    def _next_response_generation(self, user_id: str) -> int:
        """生成用户维度的回复代次。

        主要逻辑：连续对话中上一轮长回复可能仍在后台线程里运行；新一轮输入开始后，
        旧代次必须自动失效，避免旧回复在线程恢复后继续输出或覆盖新轮状态。
        参数：`user_id` 为用户编号。
        返回值：新的递增代次。
        异常情况：无。
        """

        generation = self._response_generation_by_user.get(user_id, 0) + 1
        with self._generation_lock:
            self._response_generation_by_user[user_id] = generation
            self._interrupted_generation_reason_by_user.setdefault(user_id, {})
            self._assistant_parts_by_generation.setdefault(user_id, {})[generation] = []
        return generation

    def _remember_assistant_parts(self, *, user_id: str, generation: int, parts: list[str]) -> None:
        """记录当前 generation 已经释放给输出链路的助手文本。

        主要逻辑：Vision realtime 的模型回复在后台线程里持续生成；用户打断发生在
        另一个麦克风处理线程中，因此必须把已释放文本放到 generation 共享状态里。
        参数：`user_id` 定位用户；`generation` 定位回复轮次；`parts` 为释放文本片段。
        返回值：无。
        异常情况：无。
        """

        if not parts:
            return
        with self._generation_lock:
            self._assistant_parts_by_generation.setdefault(user_id, {}).setdefault(generation, []).extend(parts)

    def _generation_finalized_reason(self, user_id: str, generation: int) -> str | None:
        """返回指定 generation 是否已经写入最终 assistant 消息。"""

        with self._generation_lock:
            return self._finalized_generation_reason_by_user.get(user_id, {}).get(generation)

    def _finalize_interrupted_generation(
        self,
        *,
        user_id: str,
        session_id: str,
        generation: int,
        reason: str,
    ) -> bool:
        """在打断发生时立即封存当前 generation 的 assistant partial。

        主要逻辑：设计文档要求打断当下就写入已生成 assistant 文本并追加
        `<用户打断>`，不能等旧模型线程自然返回。该方法保证同一 generation
        只写一次，避免旧线程完成后重复追加 messages。
        参数：`user_id/session_id/generation` 定位回复；`reason` 为打断原因。
        返回值：本次是否新写入了 assistant 消息。
        异常情况：无。
        """

        with self._generation_lock:
            finalized = self._finalized_generation_reason_by_user.setdefault(user_id, {})
            if generation in finalized:
                return False
            parts = list(self._assistant_parts_by_generation.setdefault(user_id, {}).get(generation, []))
            finalized[generation] = reason
        partial = "".join(parts)
        estimated_played = None
        estimate = getattr(self.output_service, "estimate_played_text_prefix", None)
        if callable(estimate):
            try:
                estimated_played = estimate(user_id=user_id, session_id=session_id)
            except Exception as exc:  # noqa: BLE001
                self._record_event(
                    "response.interrupted_played_text_estimate_failed",
                    user_id=user_id,
                    session_id=session_id,
                    reason=reason,
                    error=f"{type(exc).__name__}: {exc}",
                )
        played_text = partial
        unheard_text = ""
        if isinstance(estimated_played, str) and len(estimated_played) < len(partial):
            played_text = estimated_played
            unheard_text = partial[len(estimated_played) :]
        content = f"{played_text}<用户打断>{unheard_text}" if partial else ""
        self.control_service.append_message(
            user_id,
            {
                "session_id": session_id,
                "role": "assistant",
                "content": content,
                "event": "assistant_text.interrupted",
                "interrupted": True,
                "interrupted_reason": reason,
                "source": "vision_agent",
            },
        )
        self._record_event(
            "response.interrupted_message_finalized",
            user_id=user_id,
            session_id=session_id,
            reason=reason,
            generation=generation,
            content_chars=len(content),
            partial_chars=len(partial),
            played_chars=len(played_text),
            unheard_chars=len(unheard_text),
            estimated_played_chars=len(estimated_played) if isinstance(estimated_played, str) else None,
        )
        return True

    def _cleanup_generation(self, user_id: str, generation: int) -> None:
        """清理已完成 generation 的临时文本缓冲。"""

        with self._generation_lock:
            self._assistant_parts_by_generation.get(user_id, {}).pop(generation, None)
            self._finalized_generation_reason_by_user.get(user_id, {}).pop(generation, None)

    def _response_cancel_reason(self, user_id: str, generation: int) -> str | None:
        """返回指定回复代次是否已取消。

        主要逻辑：先看该代次是否被显式打断；如果当前用户已经进入更高代次，说明
        旧后台回复已被新输入取代，也必须停止。保留 `_cancelled_users` 作为生命周期
        接口的兼容兜底，但正常实时链路按 generation 判断。
        参数：`user_id` 为用户编号；`generation` 为回复代次。
        返回值：取消原因；未取消返回 None。
        异常情况：无。
        """

        interrupted = self._interrupted_generation_reason_by_user.get(user_id, {})
        if generation in interrupted:
            return interrupted[generation]
        current_generation = self._response_generation_by_user.get(user_id, 0)
        if generation < current_generation:
            return "superseded_by_new_turn"
        if user_id in self._cancelled_users:
            return self._interruption_reason_by_user.get(user_id, "user_interrupt")
        return None

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
            agent_core="VisionRealtimeAgentCore",
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
            component="VisionRealtimeAgentCore",
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
        generation: int | None = None,
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
            try:
                self.output_adapter.emit_vision_delta(
                    user_id=user_id,
                    session_id=session_id,
                    text=text,
                    final=final,
                    generation_id=generation,
                )
            except TypeError as exc:
                if "generation_id" not in str(exc):
                    raise
                self.output_adapter.emit_vision_delta(user_id=user_id, session_id=session_id, text=text, final=final)
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
                component="VisionOutputAdapter",
                error=exc,
                agent_event="output.failed",
                recoverable=True,
                fallback_text=self.RECOVERABLE_ERROR_MESSAGE,
                record={"output_context": context, "final": final},
            )
            return False

    def _run_tool_loop(self, *, user_id: str, session_id: str, transcript: str, generation: int) -> str:
        """运行 Vision Agent 工具循环。

        主要逻辑：支持 provider 通过 `stream_messages(messages, tools)` 返回
        `tool_call` 事件；每次 ToolResult 会写入消息历史，然后继续请求模型生成后续回复。
        参数：`user_id/session_id/transcript` 定位当前轮次。
        返回值：最终助手文本。
        异常情况：ToolGateway 会把工具异常转换为 ToolResult，本函数不抛业务异常。
        """

        assistant_parts: list[str] = []
        context = self.context_compiler.compile(
            ContextCompileRequest(
                mode="vision",
                provider=getattr(self.vision_model, "provider_name", "unknown"),
                model=getattr(self.vision_model, "model", "unknown"),
                user_id=user_id,
                session_id=session_id,
                base_instructions=str(getattr(self.vision_model, "prompt", VISION_AGENT_SYSTEM_PROMPT)),
                current_input={"type": "text", "transcript": transcript},
                include_tools=True,
                reason="vision_agent_turn",
                memory_service=self.memory_service,
                control_service=self.control_service,
                tool_gateway=self.tool_gateway,
                max_context_messages=self.max_context_messages,
            )
        )
        messages: list[dict[str, Any]] = list(context.messages)
        tools = list(context.tools)
        prompt = context.instructions
        previous_prompt = getattr(self.vision_model, "prompt", None)
        if previous_prompt is not None:
            setattr(self.vision_model, "prompt", prompt)
        record_context_events(recorder=self.recorder, session_id=session_id, context=context)
        dynamic_context_sources: list[dict[str, Any]] = []
        visual_context = VisualAppendContext(user_id=user_id, session_id=session_id)
        turn_visual_update = self.visual_appender.flush_turn_assets(visual_context)
        messages.extend(turn_visual_update.messages)
        dynamic_context_sources.extend(turn_visual_update.source_records)
        for event in turn_visual_update.events:
            self.recorder.record_agent_event(session_id, event)

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
                        self.vision_model.request_options_snapshot()
                        if hasattr(self.vision_model, "request_options_snapshot")
                        else {}
                    ),
                    "context_metadata": {**context.metadata, "request_reason": reason},
                },
            )

        try:
            model_vision_delta_count = 0
            model_text_chars = 0
            for iteration in range(4):
                record_current_model_request(reason=f"vision_agent_turn_iteration_{iteration + 1}")
                tool_calls: list[dict[str, Any]] = []
                model_output_started = False
                gate = TextResponseGate(
                    user_id=user_id,
                    session_id=session_id,
                    recorder=self.recorder,
                    emit=lambda text: self._emit_assistant_vision_delta(
                        user_id=user_id,
                        session_id=session_id,
                        text=text,
                        generation=generation,
                    ),
                )
                for item in self._stream_model(messages=messages, transcript=transcript, tools=tools):
                    cancel_reason = self._response_cancel_reason(user_id, generation)
                    if cancel_reason is not None:
                        self._record_event(
                            "response.interrupted",
                            user_id=user_id,
                            session_id=session_id,
                            reason=cancel_reason,
                            released_chars=sum(len(part) for part in assistant_parts),
                            buffered_chars=sum(len(part) for part in getattr(gate, "_buffer", [])),
                        )
                        return "".join(assistant_parts)
                    if isinstance(item, dict) and item.get("type") == "tool_call":
                        if not model_output_started:
                            model_output_started = True
                        released_texts, _output_ok = gate.release()
                        assistant_parts.extend(released_texts)
                        self._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
                        tool_calls.append(item)
                        self._record_event(
                            "tool_call.delta",
                            user_id=user_id,
                            session_id=session_id,
                            tool_call_id=str(item.get("id") or ""),
                            tool_name=str(item.get("name") or ""),
                        )
                        continue
                    vision_delta = self._extract_vision_delta(item)
                    if not vision_delta:
                        continue
                    model_vision_delta_count += 1
                    model_text_chars += len(vision_delta)
                    self.recorder.record_timeline_checkpoint(
                        session_id,
                        checkpoint="vision.timeline.llm.first_token",
                        user_id=user_id,
                        fields={
                            "provider": getattr(self.vision_model, "provider_name", "unknown"),
                            "model": getattr(self.vision_model, "model", "unknown"),
                            "text_preview": vision_delta[:40],
                            "delta_chars": len(vision_delta),
                        },
                    )
                    if not model_output_started:
                        model_output_started = True
                    gate.buffer(vision_delta)
                    released_texts, _output_ok = gate.release_ready(reason="vision_delta_realtime")
                    assistant_parts.extend(released_texts)
                    self._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
                if not tool_calls or self.tool_gateway is None:
                    released_texts, _output_ok = gate.release()
                    assistant_parts.extend(released_texts)
                    self._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
                    break
                released_texts, _output_ok = gate.release()
                assistant_parts.extend(released_texts)
                self._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
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
                        "source": "vision_agent",
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
                    update = self.visual_appender.append_visual_assets(
                        messages=messages,
                        tool_call=tool_call,
                        tool_result=result_dict,
                        context=visual_context,
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
                            "source": "vision_agent",
                        },
                    )
        finally:
            if previous_prompt is not None:
                setattr(self.vision_model, "prompt", previous_prompt)
        self.recorder.record_timeline_checkpoint(
            session_id,
            checkpoint="vision.timeline.llm.done",
            user_id=user_id,
            fields={
                "provider": getattr(self.vision_model, "provider_name", "unknown"),
                "model": getattr(self.vision_model, "model", "unknown"),
                "vision_delta_count": model_vision_delta_count,
                "text_chars": model_text_chars,
            },
        )
        return "".join(assistant_parts)

    def _stream_model(self, *, messages: list[dict[str, Any]], transcript: str, tools: list[dict]) -> Any:
        stream_messages = getattr(self.vision_model, "stream_messages", None)
        if callable(stream_messages):
            return stream_messages(messages=messages, tools=tools)
        return self.vision_model.stream_text(transcript)

    def _build_runtime_messages(self, *, user_id: str, session_id: str, transcript: str) -> list[dict[str, Any]]:
        """构造发送给视觉语言模型的运行时消息。

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
        """构造当前轮视觉语言模型提示词。

        主要逻辑：在静态提示词后追加长期记忆和更早历史摘要，避免把已压缩的原始
        对话重复放回 messages。
        参数：`user_id` 为当前用户编号，`session_id` 为设备级会话编号。
        返回值：发送给视觉语言模型的提示词。
        异常情况：memory 或历史摘要读取失败时跳过对应片段。
        """

        parts = [str(getattr(self.vision_model, "prompt", VISION_AGENT_SYSTEM_PROMPT))]
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
    def _extract_vision_delta(item: Any) -> str:
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

    def _emit_assistant_vision_delta(self, *, user_id: str, session_id: str, text: str, generation: int) -> bool:
        """释放一段助手文本并同步 Vision turn 状态。

        主要逻辑：文本真正进入输出链路前把状态切到 speaking；输出异常仍由
        `_emit_output_best_effort()` 转为可恢复事件。
        参数：`user_id/session_id/text` 定位本轮输出。
        返回值：输出成功返回 True。
        异常情况：不向外抛出输出异常。
        """

        self._set_turn_state(user_id, session_id, "speaking", reason="assistant_text_released")
        if text:
            self._extend_assistant_output_guard(user_id, start_ms=self._now_ms(), tail_ms=1500)
        return self._emit_output_best_effort(
            user_id=user_id,
            session_id=session_id,
            stream_id=None,
            stream_type=None,
            text=text,
            final=False,
            context="assistant_vision_delta",
            generation=generation,
        )

    @staticmethod
    def _tool_result_to_dict(result: ToolResult) -> dict:
        return {
            "ok": result.ok,
            "data": VisionRealtimeAgentCore._jsonable(result.data),
            "message": result.message,
            "assets": [VisionRealtimeAgentCore._jsonable(item) for item in result.assets or []],
            "visual_assets": [VisionRealtimeAgentCore._jsonable(item) for item in result.visual_assets or []],
            "artifacts": [VisionRealtimeAgentCore._jsonable(item) for item in result.artifacts or []],
            "status": getattr(result, "status", "completed"),
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

        return [VisionRealtimeAgentCore._redact_message_for_record(message) for message in messages]

    @staticmethod
    def _redact_message_for_record(message: dict[str, Any]) -> dict[str, Any]:
        record = dict(message)
        content = record.get("content")
        if isinstance(content, list):
            record["content"] = [VisionRealtimeAgentCore._redact_content_block(item) for item in content]
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
            return [VisionRealtimeAgentCore._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {key: VisionRealtimeAgentCore._jsonable(item) for key, item in value.items()}
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

    def on_speech_started(
        self,
        user_id: str,
        session_id: str,
        *,
        stream_id: str,
        reason: str,
        diagnostics: dict | None = None,
    ) -> None:
        """处理服务端 VAD 的语音开始事件。

        主要逻辑：Vision realtime 链路由服务端 VAD 判断用户开始说话；如果上一轮回复仍在
        生成、工具执行或播放，则取消旧回复和当前 output stream。没有活跃回复时只记录
        speech_start，不改变当前输入处理。
        参数：`user_id/session_id/stream_id` 定位音频会话；`reason` 标识触发来源；
        `diagnostics` 是 VAD 诊断。
        返回值：无。
        异常情况：取消失败由下游 provider 或 output service 记录。
        """

        active_stream_id = self.output_service.active_output_stream_id(user_id, session_id)
        state = self._state_by_user.get(user_id, "")
        should_cancel = active_stream_id is not None or state in {"thinking", "speaking", "tool_running"}
        self._record_event(
            "vision.vad.speech_started",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            output_stream_id=active_stream_id,
            state=state,
            will_cancel=should_cancel,
            diagnostics=diagnostics or {},
        )
        if not getattr(self, "_pipeline_event_control_enabled", False):
            self.control_service.publish(
                Event(
                    event_name="audio.speech.started",
                    user_id=user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    session_id=session_id,
                    payload={
                        "stream_id": stream_id,
                        "reason": reason,
                        "diagnostics": diagnostics or {},
                    },
                )
            )
        self._mark_user_activity(user_id, session_id)
        self._start_visual_sampler(user_id=user_id, session_id=session_id, stream_id=stream_id, reason=reason)
        if should_cancel and not getattr(self, "_pipeline_event_control_enabled", False):
            self.interrupt(user_id, reason=reason)

    def on_speech_stopped(
        self,
        user_id: str,
        session_id: str,
        *,
        stream_id: str,
        reason: str,
        diagnostics: dict | None = None,
    ) -> None:
        """处理服务端 VAD 的语音结束事件。

        主要逻辑：server VAD 的 speech_stop 是连续麦克风输入的一句话结束边界；
        这里会停止本轮视觉采样，并用上一片真实语音 chunk 显式提交 ASR。
        参数：`user_id/session_id/stream_id` 定位音频会话；`reason` 标识触发来源；
        `diagnostics` 是 VAD 诊断。
        返回值：无。
        异常情况：无。
        """

        self._record_event(
            "vision.vad.speech_stopped",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason=reason,
            diagnostics=diagnostics or {},
        )
        if not getattr(self, "_pipeline_event_control_enabled", False):
            self.control_service.publish(
                Event(
                    event_name="audio.speech.stopped",
                    user_id=user_id,
                    producer_id=SERVER_PRODUCER_ID,
                    session_id=session_id,
                    payload={
                        "stream_id": stream_id,
                        "reason": reason,
                        "diagnostics": diagnostics or {},
                    },
                )
            )
        self._mark_user_activity(user_id, session_id)
        self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason=reason)
        chunk = self._latest_audio_chunk_by_session.get(session_id)
        if chunk is None or chunk.stream_id != stream_id:
            self._record_event(
                "vision.asr_commit.skipped",
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                reason="latest_audio_chunk_missing",
            )
            return
        transcript = self.asr_pipeline.commit_audio(chunk)
        if transcript is None:
            self._record_event(
                "vision.asr_commit.empty",
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                reason=reason,
            )
            return
        self._handle_final_transcript(chunk=chunk, transcript=transcript)

    def on_conversation_speech_stopped(
        self,
        user_id: str,
        session_id: str,
        *,
        stream_id: str,
        reason: str,
        diagnostics: dict | None = None,
    ) -> None:
        """处理 conversation runtime 已识别的语音结束。

        主要逻辑：ASR-backed SpeechInputBoundary 已经负责提交 ASR 并产出
        `turn_ended(final_text)`，因此这里只记录统一 speech stopped 状态、刷新用户活动
        和停止本轮视觉采样，不再调用内部 ASR commit。
        参数：`user_id/session_id/stream_id` 定位音频会话；`reason` 标识触发来源；
        `diagnostics` 是 ASR 句边界诊断。
        返回值：无。
        异常情况：无。
        """

        self._record_event(
            "vision.conversation_speech.stopped",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            reason=reason,
            diagnostics=diagnostics or {},
        )
        self._mark_user_activity(user_id, session_id)
        self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason=reason)

    def on_conversation_asr_text_delta(
        self,
        user_id: str,
        session_id: str,
        *,
        stream_id: str,
        text: str,
        diagnostics: dict | None = None,
    ) -> None:
        """记录 conversation runtime 接收到的 ASR 文本增量。"""

        self._record_event(
            "vision.conversation_asr_text.delta",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            text=text,
            diagnostics=diagnostics or {},
        )

    def _start_visual_sampler(self, *, user_id: str, session_id: str, stream_id: str, reason: str) -> None:
        """启动 Vision 当前语音 turn 的视觉采样。

        主要逻辑：Vision/VL 模型不能像 Omni 一样边流式追加图片给 provider，因此这里
        只负责把服务端主动采集到的 RGB 帧写入照片资产 buffer，真正 append 发生在模型
        请求前的 `flush_turn_assets()`。
        参数：`user_id/session_id/stream_id` 定位当前音频 turn；`reason` 标识启动来源。
        返回值：无。
        异常情况：无可用 RGB 设备或配置关闭时直接跳过。
        """

        if not self.realtime_video_enabled:
            return
        if self.asset_service is None:
            return
        interval = float(self.visual_frame_interval_seconds or 0)
        if interval <= 0:
            return
        if stream_id:
            self._audio_stream_by_session[session_id] = stream_id
        existing = self._visual_sampler_threads_by_session.get(session_id)
        if existing and existing.is_alive():
            return
        if not self._has_paired_visual_capture_device(user_id=user_id, session_id=session_id):
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "vision.visual_sampler.paired_stream_unavailable",
                    "frame_index": self._visual_sampler_frame_count_by_session.get(session_id, 0),
                    "audio_stream_id": self._audio_stream_by_session.get(session_id),
                    "reason": reason,
                },
            )
            return
        self._visual_sampler_frame_count_by_session[session_id] = 0
        stop_event = threading.Event()
        self._visual_sampler_stop_by_session[session_id] = stop_event
        thread = threading.Thread(
            target=self._visual_sampler_loop,
            kwargs={"user_id": user_id, "session_id": session_id, "stop_event": stop_event, "interval": interval},
            name=f"vision-visual-{session_id}",
            daemon=True,
        )
        self._visual_sampler_threads_by_session[session_id] = thread
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "vision.visual_sampler.started",
                "interval_seconds": interval,
                "reason": reason,
            },
        )
        thread.start()

    def _stop_visual_sampler(self, *, user_id: str, session_id: str, reason: str, wait: bool = False) -> None:
        """停止 Vision 当前语音 turn 的视觉采样。"""

        stop_event = self._visual_sampler_stop_by_session.pop(session_id, None)
        thread = self._visual_sampler_threads_by_session.pop(session_id, None)
        if stop_event is None and thread is None:
            return
        if stop_event is not None:
            stop_event.set()
        if wait and thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.05, float(self.visual_frame_timeout_seconds or 0.5)))
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "vision.visual_sampler.stopped",
                "reason": reason,
                "frame_count": self._visual_sampler_frame_count_by_session.get(session_id, 0),
            },
        )
        self._request_visual_stream_close(user_id=user_id, session_id=session_id, reason=reason)

    def _request_visual_stream_close(self, *, user_id: str, session_id: str, reason: str) -> None:
        """请求端侧关闭本轮 Vision realtime 视觉采集。"""

        event = Event(
            event_name="stream.control.close.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=session_id,
            stream_type="sensor.rgb",
            payload={"stream_type": "sensor.rgb", "mode": "stop", "reason": f"vision_realtime_visual_sampler_{reason}"},
        )
        matched = self.control_service.resolve_matching_devices(event, selection="all")
        publish_result = self.control_service._push_event_to_device_ids(
            event,
            tuple(device.device_id for device in matched),
        )
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "vision.visual_stream.close.requested",
                "reason": reason,
                "matched_count": publish_result.matched_count,
                "delivered_count": publish_result.delivered_count,
                "matched_device_ids": list(publish_result.matched_device_ids),
            },
        )

    def _visual_sampler_loop(
        self,
        *,
        user_id: str,
        session_id: str,
        stop_event: threading.Event,
        interval: float,
    ) -> None:
        """按固定间隔请求 RGB 单帧写入 turn buffer。"""

        while not stop_event.is_set():
            frame_index = self._visual_sampler_frame_count_by_session.get(session_id, 0)
            if self.visual_max_frames_per_turn and frame_index >= self.visual_max_frames_per_turn:
                self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason="max_frames_per_turn")
                return
            started_at = time.monotonic()
            try:
                if not self._has_paired_visual_capture_device(user_id=user_id, session_id=session_id):
                    self.recorder.record_agent_event(
                        session_id,
                        {
                            "event": "vision.visual_sampler.paired_stream_unavailable",
                            "frame_index": frame_index,
                            "audio_stream_id": self._audio_stream_by_session.get(session_id),
                        },
                    )
                    self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason="paired_stream_unavailable")
                    return
                self._request_visual_frame_for_buffer(user_id=user_id, session_id=session_id, frame_index=frame_index)
            except Exception as exc:  # noqa: BLE001 - 后台采样不能打断音频和回复主链路
                self.recorder.record_agent_event(
                    session_id,
                    {
                        "event": "vision.visual_frame.failed",
                        "frame_index": frame_index,
                        "message": str(exc),
                    },
                )
            self._visual_sampler_frame_count_by_session[session_id] = frame_index + 1
            elapsed = time.monotonic() - started_at
            stop_event.wait(max(0.0, interval - elapsed))

    def _has_paired_visual_capture_device(self, *, user_id: str, session_id: str) -> bool:
        """检查当前音频设备是否仍在线且支持 RGB 采集。"""

        audio_stream_id = self._audio_stream_by_session.get(session_id)
        if not audio_stream_id:
            return False
        if audio_stream_id in self._closed_audio_streams_by_session.get(session_id, set()):
            return False
        event = Event(
            event_name="stream.control.open.requested",
            user_id=user_id,
            producer_id=SERVER_PRODUCER_ID,
            session_id=session_id,
            stream_type="sensor.rgb",
            payload={"stream_type": "sensor.rgb", "mode": "single", "reason": "vision_realtime_visual_sampler_probe"},
        )
        return any(
            device.device_id == session_id
            for device in self.control_service.resolve_matching_devices(event, selection="all")
        )

    def _request_visual_frame_for_buffer(self, *, user_id: str, session_id: str, frame_index: int) -> None:
        """请求一张 RGB 照片资产，等待 AssetService 写入 turn buffer。"""

        if self.asset_service is None:
            return
        asset = self.asset_service.request_asset(
            user_id=user_id,
            stream_type="sensor.rgb",
            freshness_seconds=0.0,
            params={
                "format": "jpeg",
                "frequency_hz": 1,
                "sample_count": 1,
                "duration_seconds": 0,
                "ttl_seconds": self.visual_frame_ttl_seconds,
                "capture_reason": "realtime_video",
                "direction": self.visual_direction,
            },
            session_id=session_id,
            timeout_seconds=max(0.05, float(self.visual_frame_timeout_seconds or 1.5)),
            device_ids=(session_id,),
        )
        if asset is None:
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": "vision.visual_frame.missing",
                    "frame_index": frame_index,
                    "timeout_seconds": self.visual_frame_timeout_seconds,
                },
            )
            return
        self.recorder.record_agent_event(
            session_id,
            {
                "event": "vision.visual_frame.buffered",
                "asset_id": asset.asset_id,
                "frame_index": frame_index,
                "direction": asset.metadata.get("direction") or self.visual_direction,
            },
        )

    def _maybe_capture_visual_frame_before_response(self, *, user_id: str, session_id: str) -> None:
        """在进入 VL 模型前确保短语音 turn 至少有一次采样机会。"""

        if not self.realtime_video_enabled or self.asset_service is None:
            return
        frame_count = self._visual_sampler_frame_count_by_session.get(session_id, 0)
        if frame_count > 0:
            return
        if not self._has_paired_visual_capture_device(user_id=user_id, session_id=session_id):
            return
        self._request_visual_frame_for_buffer(user_id=user_id, session_id=session_id, frame_index=0)
        self._visual_sampler_frame_count_by_session[session_id] = 1

    def _handle_asr_transcript_event(self, chunk: StreamChunk, event: Any) -> None:
        """根据 ASR 结构化事件处理 Vision 链路语音边界和打断。

        主要逻辑：Paraformer realtime 会在 `result-generated.output.sentence`
        中返回 `sentence_begin` 和 `sentence_end`。Vision realtime 以这些 provider
        VAD 字段作为主判定来源；`text` partial 只保留为非 Paraformer provider 的
        过渡兜底。后续 ASR final 仍按正常新一轮用户输入进入模型。
        参数：`chunk` 为触发 ASR 事件的麦克风分片；`event` 为 ASR transcript 事件。
        返回值：无。
        异常情况：无。
        """

        sentence_key = self._asr_sentence_key(chunk=chunk, event=event)
        if bool(getattr(event, "sentence_begin", False)) and sentence_key not in self._asr_started_sentence_keys:
            self._asr_started_sentence_keys.add(sentence_key)
            self.on_speech_started(
                chunk.user_id,
                chunk.session_id,
                stream_id=chunk.stream_id,
                reason="paraformer_sentence_begin",
                diagnostics=self._asr_boundary_diagnostics(event),
            )
            return

        if bool(getattr(event, "sentence_end", False)) and sentence_key not in self._asr_stopped_sentence_keys:
            self._asr_stopped_sentence_keys.add(sentence_key)
            self.on_speech_stopped(
                chunk.user_id,
                chunk.session_id,
                stream_id=chunk.stream_id,
                reason="paraformer_sentence_end",
                diagnostics=self._asr_boundary_diagnostics(event),
            )

        return

    def _mark_user_activity(self, user_id: str, session_id: str) -> None:
        """记录有效用户语音活动。"""

        if self._on_user_activity is not None:
            self._on_user_activity(user_id, session_id)

    def _asr_sentence_key(self, *, chunk: StreamChunk, event: Any) -> str:
        """生成 ASR 句子边界去重 key。"""

        sentence_id = getattr(event, "sentence_id", None)
        if sentence_id is None:
            sentence_id = f"seq:{chunk.seq}"
        return f"{chunk.session_id}:{chunk.stream_id}:{sentence_id}"

    def _asr_boundary_diagnostics(self, event: Any) -> dict[str, Any]:
        """生成可落盘的 ASR 句子边界诊断信息。"""

        diagnostics: dict[str, Any] = {}
        for name in (
            "sentence_id",
            "sentence_begin",
            "sentence_end",
            "begin_time_ms",
            "end_time_ms",
            "text",
        ):
            value = getattr(event, name, None)
            if value not in (None, False, ""):
                diagnostics[name] = value
        words = getattr(event, "words", None)
        if isinstance(words, list):
            diagnostics["word_count"] = len(words)
        return diagnostics

    def interrupt(self, user_id: str, *, reason: str) -> None:
        self._cancelled_users.add(user_id)
        self._interruption_reason_by_user[user_id] = reason
        generation = self._response_generation_by_user.get(user_id)
        if generation is not None:
            self._interrupted_generation_reason_by_user.setdefault(user_id, {})[generation] = reason
        session_id = self._session_by_user.get(user_id, "")
        if generation is not None and session_id:
            self._finalize_interrupted_generation(
                user_id=user_id,
                session_id=session_id,
                generation=generation,
                reason=reason,
            )
        self.vision_model.cancel()
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

        主要逻辑：取消 ASR 和视觉语言模型，清理当前用户取消标记，并记录统一关闭事件。
        参数：`user_id` 为用户标识；`reason` 为关闭原因。
        返回值：无。
        异常情况：provider cancel 异常由 provider 自身处理。
        """

        self._cancelled_users.discard(user_id)
        session_id = self._session_by_user.pop(user_id, None)
        if session_id:
            self._stop_visual_sampler(user_id=user_id, session_id=session_id, reason=reason)
            prefix = f"{session_id}:"
            self._asr_started_sentence_keys = {
                key for key in self._asr_started_sentence_keys if not key.startswith(prefix)
            }
            self._asr_stopped_sentence_keys = {
                key for key in self._asr_stopped_sentence_keys if not key.startswith(prefix)
            }
            self._audio_stream_by_session.pop(session_id, None)
            self._closed_audio_streams_by_session.pop(session_id, None)
            self._visual_sampler_frame_count_by_session.pop(session_id, None)
        self.asr_pipeline.cancel()
        self.vision_model.cancel()
        if session_id and hasattr(self.output_service, "close_text_session"):
            self.output_service.close_text_session(session_id, reason=reason)
        self._record_event("session.closed", user_id=user_id, session_id=session_id, reason=reason)

    # late result follow-up 视为空闲的 turn 状态：可以安全注入新的回复 turn。
    _FOLLOW_UP_IDLE_TURN_STATES = {"listening", "completed", "interrupted", "failed"}

    def is_session_active(self, user_id: str, session_id: str) -> bool:
        """判断某用户的 VL 会话是否仍处于活跃状态。"""

        return self._session_by_user.get(user_id) == session_id

    def is_turn_idle(self, user_id: str, session_id: str) -> bool:
        """判断当前 turn 是否空闲，可注入 late result follow-up turn。

        主要逻辑：复用 `_set_turn_state` 维护的状态机；thinking/tool_running/
        speaking/transcribing 视为忙，其余视为空闲。
        """

        key = user_id or session_id
        return self._state_by_user.get(key, "listening") in self._FOLLOW_UP_IDLE_TURN_STATES

    def inject_followup_result(self, *, user_id: str, session_id: str, text: str, run_id: str = "") -> bool:
        """把 late result 作为一次文本驱动的回复 turn 注入 VL 会话。

        主要逻辑：late result 以 `role=user` 文本写入 messages（事件
        `tool_result.late.done`），再复用现有响应 turn 机制让模型基于结果组织口语回复。
        参数：`text` 为系统包装后的工具结果文本；`run_id` 用于可观测串联。
        返回值：成功启动注入 turn 返回 True；会话不活跃返回 False。
        异常情况：底层 turn 执行异常由 `_run_response_turn` 转为可恢复回复。
        """

        if not self.is_session_active(user_id, session_id):
            return False
        wrapped = str(text or "").strip()
        if not wrapped:
            return False
        self.control_service.append_message(
            user_id,
            {
                "session_id": session_id,
                "role": "user",
                "content": wrapped,
                "event": "tool_result.late.done",
                "source": "follow_up_router",
                "tool_run_id": run_id,
            },
        )
        self._record_event(
            "tool_run.follow_up.injected",
            user_id=user_id,
            session_id=session_id,
            channel="vl_turn",
            tool_run_id=run_id,
        )
        stream_id = self._audio_stream_by_session.get(session_id) or new_id("stream_followup")
        chunk = StreamChunk(
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            stream_type="sensor.mic",
            seq=0,
            payload=b"",
            final=False,
        )
        generation = self._next_response_generation(user_id)
        self._cancelled_users.discard(user_id)
        self._set_turn_state(user_id, session_id, "thinking", reason="tool_result_late")
        thread = threading.Thread(
            target=self._run_response_turn,
            kwargs={"chunk": chunk, "transcript": wrapped, "generation": generation},
            name=f"vl-followup-{session_id}",
            daemon=True,
        )
        thread.start()
        return True

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
        """记录 Vision 链路状态机变化。

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
            agent_core="VisionRealtimeAgentCore",
            modality="vision",
            previous_state=previous,
            state=state,
            reason=reason,
        )
