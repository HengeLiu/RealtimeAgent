from __future__ import annotations

from typing import Any

from realtime_agent.agent_core.base import AgentCoreEvent
from realtime_agent.agent_core.vision import VisionRealtimeAgentCore
from realtime_agent.observability import RunRecorder
from realtime_agent.protocol import StreamChunk
from realtime_agent.realtime_pipeline.base import PipelineEvent, StreamRef
from realtime_agent.realtime_pipeline.shared import PipelineEventEmitter, RealtimeAudioNormalizer, RealtimeOutputController


class VisionInputBoundary:
    """Vision 链路输入边界组件。

    主要功能：封装上行麦克风 stream 与 Paraformer ASR provider 的绑定关系。
    主要方法：`attach_upstream()` 预热 ASR；`append_audio()` 持续写入音频；
    `detach_upstream()` 释放 ASR provider。
    主要属性：`core` 是实际持有 ASR pipeline 和语音边界解释逻辑的 `VisionRealtimeAgentCore`。
    """

    def __init__(self, *, core: VisionRealtimeAgentCore, emitter: PipelineEventEmitter) -> None:
        self.core = core
        self.emitter = emitter
        self._upstreams: dict[str, StreamRef] = {}
        self._install_core_boundary_bridge()

    def _install_core_boundary_bridge(self) -> None:
        """把 VisionRealtimeAgentCore 的语音边界事件转换为统一 PipelineEvent。

        主要逻辑：Paraformer 事件仍由 `VisionRealtimeAgentCore` 解释，避免复制 ASR 细节；
        `VisionInputBoundary` 只负责把解释后的 speech start/stop 桥接成 pipeline
        统一事件，供外层以同一种方式处理 Text 和 Omni 链路。
        """

        if getattr(self.core, "_realtime_pipeline_boundary_bridge_installed", False):
            return
        original_started = self.core.on_speech_started
        original_stopped = self.core.on_speech_stopped
        emitter = self.emitter
        core = self.core

        def _on_speech_started(user_id: str, session_id: str, *, stream_id: str, reason: str, diagnostics: dict | None = None) -> None:
            active_stream_id = core.output_service.active_output_stream_id(user_id, session_id)
            state = getattr(core, "_state_by_user", {}).get(user_id, "")
            will_cancel = active_stream_id is not None or state in {"thinking", "speaking", "tool_running"}
            setattr(core, "_pipeline_event_control_enabled", True)
            emitter.emit(
                "speech_started",
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                reason=reason,
                output_stream_id=active_stream_id,
                state=state,
                will_cancel=will_cancel,
                diagnostics=diagnostics or {},
            )
            if will_cancel:
                emitter.emit(
                    "output_cancel_requested",
                    user_id=user_id,
                    session_id=session_id,
                    stream_id=active_stream_id or "",
                    reason=reason,
                )
            original_started(user_id, session_id, stream_id=stream_id, reason=reason, diagnostics=diagnostics)

        def _on_speech_stopped(user_id: str, session_id: str, *, stream_id: str, reason: str, diagnostics: dict | None = None) -> None:
            emitter.emit(
                "speech_stopped",
                user_id=user_id,
                session_id=session_id,
                stream_id=stream_id,
                reason=reason,
                diagnostics=diagnostics or {},
            )
            original_stopped(user_id, session_id, stream_id=stream_id, reason=reason, diagnostics=diagnostics)

        self.core.on_speech_started = _on_speech_started
        self.core.on_speech_stopped = _on_speech_stopped
        setattr(self.core, "_realtime_pipeline_boundary_bridge_installed", True)

    def attach_upstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定上行麦克风连接并提前建立 ASR provider。"""

        self._upstreams[stream_ref.stream_id] = stream_ref
        self.core.on_audio_input_opened(
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
        )
        return self.emitter.emit(
            "upstream_ready",
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
        )

    def append_audio(self, chunk: StreamChunk) -> None:
        """向 VisionRealtimeAgentCore 写入一片音频。"""

        self.core.append_audio_event(chunk)

    def detach_upstream(self, stream_ref: StreamRef, *, reason: str) -> PipelineEvent:
        """关闭上行 ASR provider。"""

        self._upstreams.pop(stream_ref.stream_id, None)
        self.core.on_audio_input_closed(
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
            reason=reason,
        )
        return self.emitter.emit(
            "upstream_detached",
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
            reason=reason,
        )


class VisionResponseEngine:
    """Vision 链路回复生成组件。

    主要功能：封装 Vision 模型和 Streaming TTS 回复生成能力。当前实现复用
    `VisionRealtimeAgentCore` 内部成熟的工具循环、上下文编译和文本输出逻辑。
    主要方法：`prepare_session()` 记录回复引擎准备；`cancel_current_response()`
    取消当前 generation；`close()` 释放模型和 TTS 相关状态。
    """

    def __init__(self, *, core: VisionRealtimeAgentCore, output_controller: RealtimeOutputController, emitter: PipelineEventEmitter) -> None:
        self.core = core
        self.output_controller = output_controller
        self.emitter = emitter
        self._text_sessions: set[str] = set()
        self._install_output_bridge()

    def _install_output_bridge(self) -> None:
        """把 Vision TTS 输出事件桥接为统一 PipelineEvent。"""

        output_service = self.output_controller.output_service
        add_delta_listener = getattr(output_service, "add_output_audio_delta_listener", None)
        if callable(add_delta_listener):
            add_delta_listener(self._on_output_audio_delta)
        add_finish_listener = getattr(output_service, "add_output_finished_listener", None)
        if callable(add_finish_listener):
            add_finish_listener(self._on_output_finished)

    def _on_output_audio_delta(self, record: dict[str, Any]) -> None:
        """把 OutputService 已写入的 Text 音频分片转成 pipeline 事件。"""

        session_id = str(record.get("session_id") or "")
        if session_id not in self._text_sessions:
            return
        self.emitter.emit(
            "output_audio_delta",
            user_id=str(record.get("user_id") or ""),
            session_id=session_id,
            stream_id=str(record.get("stream_id") or ""),
            payload_size=record.get("payload_size"),
            chunk_count=record.get("chunk_count"),
            source="vision_tts",
        )

    def _on_output_finished(self, user_id: str, session_id: str, stream_id: str) -> None:
        """把 OutputService 完成回调转成 pipeline output_finished。"""

        if session_id not in self._text_sessions:
            return
        self.emitter.emit(
            "output_finished",
            user_id=user_id,
            session_id=session_id,
            stream_id=stream_id,
            source="vision_tts",
        )

    def prepare_session(self, *, user_id: str, session_id: str) -> PipelineEvent:
        """记录 Text 回复引擎已准备好。"""

        return self.emitter.emit("response_engine_ready", user_id=user_id, session_id=session_id)

    def attach_downstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定下行 stream 并预热 TTS。"""

        self.output_controller.bind_downstream(
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
            reason="pipeline_attach_downstream",
        )
        self._text_sessions.add(stream_ref.session_id)
        return self.emitter.emit(
            "downstream_ready",
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
        )

    def cancel_current_response(self, *, user_id: str, reason: str) -> None:
        """取消当前 Vision 模型响应。"""

        self.core.interrupt(user_id, reason=reason)

    def prepare_close(self, *, user_id: str, session_id: str, reason: str) -> PipelineEvent:
        """准备关闭连续对话，阻止新输出进入。"""

        self.output_controller.stop_accepting_new_output(session_id=session_id, reason=reason)
        return self.emitter.emit("close_prepared", user_id=user_id, session_id=session_id, reason=reason)

    def close(self, *, user_id: str, reason: str) -> None:
        """关闭 Text 回复引擎。"""

        session_id = getattr(self.core, "_session_by_user", {}).get(user_id)
        if session_id:
            self._text_sessions.discard(session_id)
        self.core.close(user_id, reason=reason)


class VisionRealtimePipeline:
    """Vision legacy realtime 音频对话 pipeline。

    主要功能：作为 `agent.conversation.runtime=legacy` 时的 Vision 链路兼容包装，
    把现有 `VisionRealtimeAgentCore`、ASR、视觉语言模型、TTS 和 OutputService
    组合成旧 pipeline 组件。新能力应优先落在 `conversation/` 目录。
    主要方法：对外提供 AgentCore 兼容生命周期方法和旧 pipeline 专用方法。
    主要属性：`core/input_boundary/response_engine/output_controller/emitter` 分别对应
    旧 pipeline 设计时序图中的真实组件。
    """

    _LOCAL_ATTRS = {
        "core",
        "normalizer",
        "input_boundary",
        "response_engine",
        "output_controller",
        "emitter",
        "_session_by_user",
        "_downstream_by_session",
    }

    def __init__(
        self,
        *,
        core: VisionRealtimeAgentCore,
        output_controller: RealtimeOutputController,
        recorder: RunRecorder,
        normalizer: RealtimeAudioNormalizer | None = None,
    ) -> None:
        object.__setattr__(self, "core", core)
        emitter = PipelineEventEmitter(recorder=recorder)
        object.__setattr__(self, "emitter", emitter)
        object.__setattr__(self, "output_controller", output_controller)
        object.__setattr__(self, "normalizer", normalizer or RealtimeAudioNormalizer())
        object.__setattr__(self, "input_boundary", VisionInputBoundary(core=core, emitter=emitter))
        object.__setattr__(
            self,
            "response_engine",
            VisionResponseEngine(core=core, output_controller=output_controller, emitter=emitter),
        )
        object.__setattr__(self, "_session_by_user", {})
        object.__setattr__(self, "_downstream_by_session", {})

    def __getattr__(self, name: str) -> Any:
        """把旧调用点透明代理到内部 VisionRealtimeAgentCore。

        主要逻辑：现有测试和少量应用代码会直接访问 `vision_model/asr_pipeline` 等属性；
        pipeline 落地阶段保留这些入口，避免把重构扩散到无关模块。
        """

        return getattr(self.core, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """把旧 VisionRealtimeAgentCore 属性写入代理到内部 core。"""

        if name in self._LOCAL_ATTRS:
            object.__setattr__(self, name, value)
            return
        setattr(self.core, name, value)

    def bind_pipeline_event_handler(self, handler) -> None:
        """绑定 RealtimeAgentApp 的 PipelineEvent 控制面处理器。"""

        self.emitter.add_listener(handler)

    def open_session(self, user_id: str, session_id: str) -> PipelineEvent:
        """打开 Vision realtime 连续对话 session。"""

        self._session_by_user[user_id] = session_id
        self.core.open(user_id, session_id)
        self.response_engine.prepare_session(user_id=user_id, session_id=session_id)
        return self.emitter.emit("session_ready", user_id=user_id, session_id=session_id)

    def attach_upstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定上行麦克风连接。"""

        return self.input_boundary.attach_upstream(stream_ref)

    def attach_downstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定下行扬声器连接。"""

        self._downstream_by_session[stream_ref.session_id] = stream_ref.stream_id
        return self.response_engine.attach_downstream(stream_ref)

    def append_input_audio(self, chunk: StreamChunk) -> list[PipelineEvent]:
        """追加上行音频。

        主要逻辑：音频格式归一化仍由现有 `AudioPipeline` 执行；这里负责进入 Text
        pipeline 的输入边界组件。
        """

        before = len(self.emitter.events())
        normalized = self.normalizer.process(chunk)
        self.emitter.emit(
            "input_audio_normalized",
            user_id=chunk.user_id,
            session_id=chunk.session_id,
            stream_id=chunk.stream_id,
            record=False,
            diagnostics=normalized.diagnostics,
        )
        self.input_boundary.append_audio(normalized.chunk)
        return self.emitter.events()[before:]

    def pause_downstream(self, user_id: str, session_id: str) -> PipelineEvent:
        """处理端侧下行高水位暂停请求。"""

        self.output_controller.pause(user_id=user_id, session_id=session_id)
        return self.emitter.emit("downstream_paused", user_id=user_id, session_id=session_id)

    def resume_downstream(self, user_id: str, session_id: str) -> PipelineEvent:
        """处理端侧下行低水位恢复请求。"""

        self.output_controller.resume(user_id=user_id, session_id=session_id)
        return self.emitter.emit("downstream_resumed", user_id=user_id, session_id=session_id)

    def notify_output_finished(self, stream_id: str) -> PipelineEvent:
        """记录端侧 output stream 播放完成。"""

        return self.emitter.emit("output_finished_notified", stream_id=stream_id)

    def detach_upstream(self, stream_ref: StreamRef, *, reason: str) -> PipelineEvent:
        """解绑上行麦克风连接。"""

        return self.input_boundary.detach_upstream(stream_ref, reason=reason)

    def prepare_close(self, user_id: str, session_id: str, *, reason: str) -> PipelineEvent:
        """准备关闭连续对话。"""

        return self.response_engine.prepare_close(user_id=user_id, session_id=session_id, reason=reason)

    def close_session(self, user_id: str, *, reason: str) -> PipelineEvent:
        """关闭 Vision realtime 连续对话 session。"""

        session_id = self._session_by_user.get(user_id, "")
        if session_id:
            self.prepare_close(user_id, session_id, reason=reason)
        self.response_engine.close(user_id=user_id, reason=reason)
        self._session_by_user.pop(user_id, None)
        return self.emitter.emit("session_closed", user_id=user_id, session_id=session_id, reason=reason)

    def open(self, user_id: str, session_id: str) -> None:
        """AgentCore 兼容入口：打开 session。"""

        self.open_session(user_id, session_id)

    def append_audio_event(self, chunk: StreamChunk) -> None:
        """AgentCore 兼容入口：追加音频。"""

        self.append_input_audio(chunk)

    def commit_input(self, user_id: str, session_id: str, *, reason: str = "endpoint_commit") -> None:
        """AgentCore 兼容入口：提交输入。"""

        self.core.commit_input(user_id, session_id, reason=reason)

    def interrupt(self, user_id: str, *, reason: str) -> None:
        """AgentCore 兼容入口：取消当前响应。"""

        self.response_engine.cancel_current_response(user_id=user_id, reason=reason)

    def close(self, user_id: str, *, reason: str) -> None:
        """AgentCore 兼容入口：关闭 session。"""

        self.close_session(user_id, reason=reason)

    def events(self) -> list[AgentCoreEvent]:
        """返回内部 VisionRealtimeAgentCore 事件快照。"""

        return self.core.events()

    def on_audio_input_opened(self, *, user_id: str, session_id: str, stream_id: str) -> None:
        """App 兼容入口：上行麦克风 stream 已建立。"""

        self.attach_upstream(StreamRef(user_id=user_id, session_id=session_id, stream_id=stream_id, stream_type="sensor.mic"))

    def on_audio_input_closed(self, *, user_id: str, session_id: str, stream_id: str, reason: str) -> None:
        """App 兼容入口：上行麦克风 stream 已关闭。"""

        self.detach_upstream(
            StreamRef(user_id=user_id, session_id=session_id, stream_id=stream_id, stream_type="sensor.mic"),
            reason=reason,
        )

    def on_downstream_opened(self, *, user_id: str, session_id: str, stream_id: str, stream_type: str = "actuator.speaker") -> None:
        """App 入口：下行扬声器连接已建立。"""

        self.attach_downstream(StreamRef(user_id=user_id, session_id=session_id, stream_id=stream_id, stream_type=stream_type))

    def bind_tool_gateway(self, tool_gateway) -> None:
        """绑定工具网关。"""

        self.core.bind_tool_gateway(tool_gateway)

    def bind_user_activity_callback(self, callback) -> None:
        """绑定有效用户语音活动回调。"""

        self.core.bind_user_activity_callback(callback)
