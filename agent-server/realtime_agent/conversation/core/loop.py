from __future__ import annotations

import threading
from typing import Any, Callable

from realtime_agent.agent_core.context import ContextCompileRequest, record_context_events
from realtime_agent.agent_core.omni import OmniRealtimeAgentCore, RealtimeProviderCallbacks
from realtime_agent.agent_core.providers import VISION_AGENT_SYSTEM_PROMPT
from realtime_agent.agent_core.vision import (
    TextResponseGate,
    VisionRealtimeAgentCore,
    _audit_tool_calls,
    _provider_tool_call_message,
    _provider_tool_result_message,
)
from realtime_agent.agent_core.visual import VisualAppendContext
from realtime_agent.conversation.core.base import ConversationContext
from realtime_agent.conversation.types import SpeechInputDelta
from realtime_agent.protocol import SERVER_PRODUCER_ID, Event, StreamChunk


class OmniRealtimeLoop:
    """Omni Realtime AgentLoop 适配器。

    主要功能：把 Omni Manual 链路中“音频写入 provider、turn 结束后 commit、
    create_response”的 provider loop 行为从 runtime 中抽出。
    主要属性：`core` 是当前仍承载 provider 连接、工具桥和输出处理的旧
    `OmniRealtimeAgentCore`。
    """

    def __init__(self, *, core: OmniRealtimeAgentCore) -> None:
        self.core = core

    def run(self, context: ConversationContext) -> None:
        """执行 Omni loop。

        当前 Omni Realtime provider 是持续连接模式，真正的运行由 `consume_input()`
        里的 audio append 和 turn commit 驱动；这里保留接口以满足 AgentLoopABC。
        """

    def consume_input(self, delta: SpeechInputDelta) -> None:
        """消费 Omni 输入增量。

        主要逻辑：`audio_chunk` 直接 append 到 Omni provider；`turn_ended` 执行
        manual commit 和 response create；其他 delta 由 turn controller 或上层处理。
        参数：`delta` 为标准输入增量。
        返回值：无。
        异常情况：底层 provider/core 异常向上传播。
        """

        if delta.kind == "audio_chunk" and delta.audio is not None:
            self.core.append_audio_event(delta.audio)
            return
        if delta.kind == "turn_ended":
            self.commit_and_create_response(
                user_id=delta.user_id or "",
                session_id=delta.session_id,
                reason="conversation_vad_speech_stopped",
            )

    def commit_and_create_response(self, *, user_id: str, session_id: str, reason: str) -> None:
        """提交当前 Omni input buffer 并创建响应。"""

        self.core.commit_input(user_id, session_id, reason=reason)
        self.core.on_conversation_input_committed(session_id=session_id, reason=reason)
        self.core.create_response(user_id, session_id, reason=reason)

    def provider_callbacks(self, *, user_id: str, session_id: str) -> RealtimeProviderCallbacks:
        """构造 Omni realtime provider 回调。

        主要逻辑：conversation runtime 下由 AgentLoop 作为 provider event loop
        owner，负责把 provider audio/tool/error 事件接入输出、工具和观测链路。
        目前底层状态更新仍复用旧 core helper。
        参数：`user_id/session_id` 定位 provider 会话。
        返回值：`RealtimeProviderCallbacks`。
        异常情况：底层 core handler 异常按原有语义处理。
        """

        core = self.core
        return RealtimeProviderCallbacks(
            audio_delta=lambda audio, fmt, metadata: core._handle_provider_audio_delta(
                user_id=user_id,
                session_id=session_id,
                audio=audio,
                format=fmt,
                metadata=metadata,
            ),
            audio_done=lambda metadata: core._handle_provider_audio_done(
                user_id=user_id,
                session_id=session_id,
                metadata=metadata,
            ),
            provider_event=lambda record: core._record_provider_event(
                user_id=user_id,
                session_id=session_id,
                record=record,
            ),
            error=lambda message, record: core._mark_session_failed(
                user_id=user_id,
                session_id=session_id,
                message=message,
                record=record,
            ),
            tool_call_delta=lambda record: core._handle_provider_tool_call_delta(record),
            tool_call_done=lambda record: core._handle_provider_tool_call_done(
                user_id=user_id,
                session_id=session_id,
                record=record,
            ),
            replay_audio_for_tool_result=lambda result: core._replay_audio_for_tool_result(
                session_id=session_id,
                result=result,
            ),
        )

    def interrupt(self, reason: str) -> None:
        """AgentLoopABC 中断入口。

        Omni 取消需要 user_id，因此由 runtime 的 `interrupt()` 继续负责携带用户维度
        调用旧 core；这里保留接口以表达 loop 可中断。
        """


class VlAgentLoop:
    """VL AgentLoop 适配器。

    主要功能：把 VL 链路中 ASR 文本增量、final_text 到 VLM/TTS 生成的流程从
    conversation runtime 中抽出。
    主要属性：`core` 是当前仍承载消息上下文、工具循环、视觉资产和 TTS 输出的
    `VisionRealtimeAgentCore`；`stream_id_for_session` 用于补齐 ASR delta 的 stream。
    """

    def __init__(
        self,
        *,
        core: VisionRealtimeAgentCore,
        stream_id_for_session: Callable[[str], str],
    ) -> None:
        self.core = core
        self._stream_id_for_session = stream_id_for_session
        self._latest_audio_by_session: dict[str, StreamChunk] = {}

    def run(self, context: ConversationContext) -> None:
        """执行 VL loop。

        当前 VL loop 由 `consume_input()` 接收 ASR final text 后触发 provider 调用；
        这里保留接口以满足 AgentLoopABC。
        """

    def consume_input(self, delta: SpeechInputDelta) -> None:
        """消费 VL 输入增量。

        主要逻辑：缓存最新音频片用于 final_text 提交；ASR 文本增量同步给旧 core；
        `turn_ended` 携带 final_text 时触发 VLM + TTS 回复。
        参数：`delta` 为标准输入增量。
        返回值：无。
        异常情况：底层 provider/core 异常向上传播。
        """

        if delta.kind == "audio_chunk" and delta.audio is not None:
            self._latest_audio_by_session[delta.session_id] = delta.audio
            self.core._latest_audio_chunk_by_session[delta.session_id] = delta.audio
            return
        if delta.kind == "asr_text_delta":
            self.core.on_conversation_asr_text_delta(
                delta.user_id or "",
                delta.session_id,
                stream_id=delta.stream_id or self._stream_id_for_session(delta.session_id),
                text=delta.text_delta or "",
                diagnostics=dict(delta.metadata),
            )
            return
        if delta.kind == "turn_ended":
            self.handle_final_text(delta, reason="conversation_asr_speech_stopped")

    def handle_final_text(self, delta: SpeechInputDelta, *, reason: str) -> None:
        """把 ASR final text 交给 VL provider loop 生成回复。

        主要逻辑：在 loop 层完成 final_text 空值过滤、echo guard、消息落盘、响应
        started/completed 控制事件和 provider/tool loop 调用。旧 core 只作为底层
        provider、工具、视觉资产和输出能力宿主。
        """

        chunk = self._latest_audio_by_session.get(delta.session_id)
        if chunk is None:
            return
        text = (delta.final_text or "").strip()
        if not text:
            self.core._record_event(
                "vision.conversation_final_text.empty",
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                stream_id=chunk.stream_id,
                reason=reason,
            )
            return
        self.core._record_event(
            "vision.conversation_final_text.received",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_id=chunk.stream_id,
            reason=reason,
            text_chars=len(text),
        )
        self._handle_final_transcript(chunk=chunk, transcript=text)

    def _handle_final_transcript(self, *, chunk: StreamChunk, transcript: str) -> None:
        """处理 ASR 最终文本并触发 VL 回复。"""

        core = self.core
        if core._should_ignore_transcript_as_echo(chunk=chunk, transcript=transcript):
            core._stop_visual_sampler(user_id=chunk.user_id, session_id=chunk.session_id, reason="echo_guard_ignored")
            core._set_turn_state(chunk.user_id, chunk.session_id, "completed", reason="echo_guard_ignored")
            return
        core._set_turn_state(chunk.user_id, chunk.session_id, "thinking", reason="transcript_final")
        core._mark_user_activity(chunk.user_id, chunk.session_id)
        turn_key = core._turn_key(chunk=chunk, transcript=transcript)
        if turn_key in core._responded_input_streams:
            core._record_duplicate_turn(chunk=chunk, transcript=transcript, turn_key=turn_key, reason="duplicate_turn")
            core._stop_visual_sampler(user_id=chunk.user_id, session_id=chunk.session_id, reason="duplicate_turn")
            core._set_turn_state(chunk.user_id, chunk.session_id, "completed", reason="duplicate_turn")
            return
        core._responded_input_streams.add(turn_key)
        generation = core._next_response_generation(chunk.user_id)
        core._cancelled_users.discard(chunk.user_id)
        core._interruption_reason_by_user.pop(chunk.user_id, None)
        core.control_service.append_message(
            chunk.user_id,
            {
                "session_id": chunk.session_id,
                "role": "user",
                "content": transcript,
                "event": "input_transcript.done",
            },
        )
        core.control_service.publish(
            Event(
                event_name="agent.response.started",
                user_id=chunk.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=chunk.session_id,
                payload={"agent_core": "VisionRealtimeAgentCore", "agent_loop": "VlAgentLoop"},
            )
        )
        core._record_event(
            "response.started",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            agent_core="VisionRealtimeAgentCore",
            agent_loop="VlAgentLoop",
        )
        core._stop_visual_sampler(user_id=chunk.user_id, session_id=chunk.session_id, reason="transcript_final", wait=True)
        core._maybe_capture_visual_frame_before_response(user_id=chunk.user_id, session_id=chunk.session_id)
        if not chunk.final:
            thread = threading.Thread(
                target=self._run_response_turn,
                kwargs={"chunk": chunk, "transcript": transcript, "generation": generation},
                name=f"vl-response-{chunk.session_id}",
                daemon=True,
            )
            thread.start()
            return
        self._run_response_turn(chunk=chunk, transcript=transcript, generation=generation)

    def _run_response_turn(self, *, chunk: StreamChunk, transcript: str, generation: int) -> None:
        """执行 VL 模型、工具和输出回复。"""

        core = self.core
        response_error: Exception | None = None
        try:
            assistant_text = self._run_tool_loop(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                transcript=transcript,
                generation=generation,
            )
        except Exception as exc:  # noqa: BLE001 - 保留旧 core 的可恢复输出语义
            response_error = exc
            assistant_text = core.RECOVERABLE_ERROR_MESSAGE
            core._handle_response_error(chunk=chunk, error=exc, fallback_text=assistant_text)
            core._emit_output_best_effort(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                stream_id=chunk.stream_id,
                stream_type=chunk.stream_type,
                text=assistant_text,
                final=False,
                context="fallback_text",
                generation=generation,
            )
        interrupted_reason = core._response_cancel_reason(chunk.user_id, generation)
        core._interrupted_generation_reason_by_user.get(chunk.user_id, {}).pop(generation, None)
        if core._response_generation_by_user.get(chunk.user_id) == generation:
            core._interruption_reason_by_user.pop(chunk.user_id, None)
        if interrupted_reason and assistant_text and not assistant_text.endswith("<用户打断>"):
            assistant_text = f"{assistant_text}<用户打断>"
        if interrupted_reason is None:
            core._emit_output_best_effort(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                stream_id=chunk.stream_id,
                stream_type=chunk.stream_type,
                text="",
                final=True,
                context="final_flush",
                generation=generation,
            )
        message_already_finalized = core._generation_finalized_reason(chunk.user_id, generation) is not None
        if not message_already_finalized:
            core.control_service.append_message(
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
        core.control_service.publish(
            Event(
                event_name="agent.response.completed",
                user_id=chunk.user_id,
                producer_id=SERVER_PRODUCER_ID,
                session_id=chunk.session_id,
                payload={"agent_core": "VisionRealtimeAgentCore", "agent_loop": "VlAgentLoop"},
            )
        )
        core._record_event(
            "response.done",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            agent_core="VisionRealtimeAgentCore",
            agent_loop="VlAgentLoop",
            assistant_text=assistant_text,
            recovered_from_error=response_error is not None,
            interrupted=bool(interrupted_reason),
            interrupted_reason=interrupted_reason,
            message_already_finalized=message_already_finalized,
        )
        core._cleanup_generation(chunk.user_id, generation)
        core._set_turn_state(
            chunk.user_id,
            chunk.session_id,
            "interrupted" if interrupted_reason else ("failed" if response_error is not None else "completed"),
            reason=interrupted_reason or ("response_error" if response_error is not None else "response_done"),
        )
        if core.asset_service is not None:
            core.asset_service.clear_turn_buffer(
                user_id=chunk.user_id,
                session_id=chunk.session_id,
                reason=interrupted_reason or ("response_error" if response_error is not None else "response_done"),
            )
        if not interrupted_reason:
            core._extend_assistant_output_guard(chunk.user_id, start_ms=None, tail_ms=1500)

    def _run_tool_loop(self, *, user_id: str, session_id: str, transcript: str, generation: int) -> str:
        """运行 VL provider 和工具循环。

        主要逻辑：由 AgentLoop 负责准备上下文、调用 VLM provider、处理
        tool_call、回填 tool_result，并逐步释放助手文本到输出链路。旧 core 只提供
        provider、context compiler、visual appender、recorder 和 ToolGateway 等底层能力。
        参数：`user_id/session_id/transcript/generation` 定位当前响应轮次。
        返回值：本轮最终助手文本。
        异常情况：provider 或输出异常由上层 `_run_response_turn()` 转为可恢复回复。
        """

        core = self.core
        assistant_parts: list[str] = []
        context = core.context_compiler.compile(
            ContextCompileRequest(
                mode="vision",
                provider=getattr(core.vision_model, "provider_name", "unknown"),
                model=getattr(core.vision_model, "model", "unknown"),
                user_id=user_id,
                session_id=session_id,
                base_instructions=str(getattr(core.vision_model, "prompt", VISION_AGENT_SYSTEM_PROMPT)),
                current_input={"type": "text", "transcript": transcript},
                include_tools=True,
                reason="vision_agent_turn",
                memory_service=core.memory_service,
                control_service=core.control_service,
                tool_gateway=core.tool_gateway,
                max_context_messages=core.max_context_messages,
            )
        )
        messages: list[dict[str, Any]] = list(context.messages)
        tools = list(context.tools)
        prompt = context.instructions
        previous_prompt = getattr(core.vision_model, "prompt", None)
        if previous_prompt is not None:
            setattr(core.vision_model, "prompt", prompt)
        record_context_events(recorder=core.recorder, session_id=session_id, context=context)
        dynamic_context_sources: list[dict[str, Any]] = []
        visual_context = VisualAppendContext(user_id=user_id, session_id=session_id)
        turn_visual_update = core.visual_appender.flush_turn_assets(visual_context)
        messages.extend(turn_visual_update.messages)
        dynamic_context_sources.extend(turn_visual_update.source_records)
        for event in turn_visual_update.events:
            core.recorder.record_agent_event(session_id, event)

        def record_current_model_request(*, reason: str) -> None:
            core.recorder.record_model_request(
                session_id,
                {
                    "provider": context.provider,
                    "model": context.model,
                    "runner": "vl_agent_loop",
                    "user_id": user_id,
                    "session_id": session_id,
                    "prompt": prompt,
                    "messages": [{"role": "system", "content": prompt}, *core._model_request_messages(messages)],
                    "tools": tools,
                    "tool_count": len(tools),
                    "prompts": context.prompt_records(),
                    "context_sources": [*context.source_records(), *dynamic_context_sources],
                    "warnings": context.warnings,
                    "truncations": context.truncations,
                    "notifications": context.notifications,
                    "provider_request_options": (
                        core.vision_model.request_options_snapshot()
                        if hasattr(core.vision_model, "request_options_snapshot")
                        else {}
                    ),
                    "context_metadata": {**context.metadata, "request_reason": reason},
                },
            )

        try:
            model_vision_delta_count = 0
            model_text_chars = 0
            for iteration in range(4):
                record_current_model_request(reason=f"vl_agent_loop_iteration_{iteration + 1}")
                tool_calls: list[dict[str, Any]] = []
                model_output_started = False
                gate = TextResponseGate(
                    user_id=user_id,
                    session_id=session_id,
                    recorder=core.recorder,
                    emit=lambda text: core._emit_assistant_vision_delta(
                        user_id=user_id,
                        session_id=session_id,
                        text=text,
                        generation=generation,
                    ),
                )
                for item in core._stream_model(messages=messages, transcript=transcript, tools=tools):
                    cancel_reason = core._response_cancel_reason(user_id, generation)
                    if cancel_reason is not None:
                        core._record_event(
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
                        core._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
                        tool_calls.append(item)
                        core._record_event(
                            "tool_call.delta",
                            user_id=user_id,
                            session_id=session_id,
                            tool_call_id=str(item.get("id") or ""),
                            tool_name=str(item.get("name") or ""),
                        )
                        continue
                    vision_delta = core._extract_vision_delta(item)
                    if not vision_delta:
                        continue
                    model_vision_delta_count += 1
                    model_text_chars += len(vision_delta)
                    core.recorder.record_timeline_checkpoint(
                        session_id,
                        checkpoint="vision.timeline.llm.first_token",
                        user_id=user_id,
                        fields={
                            "provider": getattr(core.vision_model, "provider_name", "unknown"),
                            "model": getattr(core.vision_model, "model", "unknown"),
                            "text_preview": vision_delta[:40],
                            "delta_chars": len(vision_delta),
                        },
                    )
                    if not model_output_started:
                        model_output_started = True
                    gate.buffer(vision_delta)
                    released_texts, _output_ok = gate.release_ready(reason="vision_delta_realtime")
                    assistant_parts.extend(released_texts)
                    core._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
                if not tool_calls or core.tool_gateway is None:
                    released_texts, _output_ok = gate.release()
                    assistant_parts.extend(released_texts)
                    core._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
                    break
                released_texts, _output_ok = gate.release()
                assistant_parts.extend(released_texts)
                core._remember_assistant_parts(user_id=user_id, generation=generation, parts=released_texts)
                provider_tool_call_message = _provider_tool_call_message(tool_calls)
                messages.append(provider_tool_call_message)
                core.control_service.append_message(
                    user_id,
                    {
                        "session_id": session_id,
                        "role": "assistant",
                        "content": "",
                        "tool_calls": _audit_tool_calls(tool_calls),
                        "event": "assistant_tool_call.done",
                        "source": "vl_agent_loop",
                    },
                )
                for tool_call in tool_calls:
                    if not gate.emitted_text:
                        core.tool_gateway.emit_progress_once(
                            name=str(tool_call.get("name") or ""),
                            user_id=user_id,
                            session_id=session_id,
                            output_service=core.output_service,
                        )
                    core._set_turn_state(user_id, session_id, "tool_running", reason=str(tool_call.get("name") or "tool_call"))
                    result = core._call_tool(
                        name=str(tool_call.get("name") or ""),
                        user_id=user_id,
                        session_id=session_id,
                        input_data=dict(tool_call.get("arguments") or {}),
                    )
                    core._set_turn_state(user_id, session_id, "thinking", reason="tool_result_returned")
                    result_dict = core._tool_result_to_dict(result)
                    core.recorder.record_agent_event(
                        session_id,
                        {
                            "event": "context.source.added",
                            "source_id": f"tool_result:{tool_call.get('name') or ''}",
                            "source_kind": "tool",
                            "source_name": f"tool_result:{tool_call.get('name') or ''}",
                            "included": True,
                            "reason": "vl_agent_loop_provider_message",
                        },
                    )
                    messages.append(_provider_tool_result_message(tool_call=tool_call, result=result_dict))
                    update = core.visual_appender.append_visual_assets(
                        messages=messages,
                        tool_call=tool_call,
                        tool_result=result_dict,
                        context=visual_context,
                    )
                    messages.extend(update.messages)
                    dynamic_context_sources.extend(update.source_records)
                    for event in update.events:
                        core.recorder.record_agent_event(session_id, event)
                    core.control_service.append_message(
                        user_id,
                        {
                            "session_id": session_id,
                            "role": "tool",
                            "tool_call_id": tool_call.get("id"),
                            "name": tool_call.get("name"),
                            "content": result_dict,
                            "event": "tool_result.done",
                            "source": "vl_agent_loop",
                        },
                    )
        finally:
            if previous_prompt is not None:
                setattr(core.vision_model, "prompt", previous_prompt)
        core.recorder.record_timeline_checkpoint(
            session_id,
            checkpoint="vision.timeline.llm.done",
            user_id=user_id,
            fields={
                "provider": getattr(core.vision_model, "provider_name", "unknown"),
                "model": getattr(core.vision_model, "model", "unknown"),
                "vision_delta_count": model_vision_delta_count,
                "text_chars": model_text_chars,
            },
        )
        return "".join(assistant_parts)

    def close_session(self, session_id: str) -> None:
        """清理指定会话的 loop 缓存。"""

        self._latest_audio_by_session.pop(session_id, None)

    def interrupt(self, reason: str) -> None:
        """AgentLoopABC 中断入口。

        VL 取消需要 user_id，因此由 runtime 的 `interrupt()` 继续负责携带用户维度
        调用旧 core；这里保留接口以表达 loop 可中断。
        """
