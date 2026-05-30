from __future__ import annotations

from typing import Any, Callable

from realtime_agent.agent_core.base import AgentCoreEvent
from realtime_agent.agent_core.omni import OmniRealtimeAgentCore, RealtimeProviderConfig
from realtime_agent.asset.service import AssetService
from realtime_agent.control import ControlService
from realtime_agent.observability import RunRecorder
from realtime_agent.output import OutputService
from realtime_agent.protocol import StreamChunk
from realtime_agent.realtime_pipeline.base import PipelineEvent, StreamRef
from realtime_agent.realtime_pipeline.shared import PipelineEventEmitter, RealtimeAudioNormalizer, RealtimeOutputController
from realtime_agent.tools import ToolGateway


class OmniAgentCore(OmniRealtimeAgentCore):
    """Omni Realtime 链路的 Agent Core。

    主要功能：作为设计文档中的 `OmniAgentCore` 真实代码组件，复用现有
    `OmniRealtimeAgentCore` 的 provider 连接、工具调用、视觉帧追加和消息记录能力。
    主要方法：继承 `open()`、`append_audio_event()`、`interrupt()`、`close()`。
    主要属性：继承 provider session、generation、assistant buffer 等运行状态。
    """


class OmniInputBoundary:
    """Omni 链路输入边界组件。

    主要功能：解释 Omni provider speech 事件，把 provider 原始事件转换成统一
    pipeline `speech_started/speech_stopped` 事件。
    主要方法：`attach_upstream()` 绑定麦克风 stream；`append_audio()` 写入音频；
    `_install_provider_event_bridge()` 安装 provider 事件桥。
    """

    def __init__(self, *, core: OmniAgentCore, emitter: PipelineEventEmitter, output_controller: RealtimeOutputController) -> None:
        self.core = core
        self.emitter = emitter
        self.output_controller = output_controller
        self._upstreams: dict[str, StreamRef] = {}
        self._install_provider_event_bridge()

    def _install_provider_event_bridge(self) -> None:
        """安装 Omni provider 事件到 PipelineEvent 的转换桥。"""

        if getattr(self.core, "_omni_pipeline_boundary_bridge_installed", False):
            return
        original = self.core._record_provider_event
        emitter = self.emitter
        output_controller = self.output_controller
        core = self.core

        def _record_provider_event(*, user_id: str, session_id: str, record: dict[str, Any]) -> None:
            event = str(record.get("event") or "")
            if event == "omni.input_audio_buffer.speech_started":
                active_stream_id = output_controller.active_output_stream_id(user_id=user_id, session_id=session_id)
                state = getattr(core, "_state_by_session", {}).get(session_id, "")
                has_active_output = active_stream_id is not None
                interruptible_state = state in {"thinking", "speaking", "tool_running"}
                interruptible = has_active_output or interruptible_state
                setattr(core, "_pipeline_event_control_enabled", True)
                emitter.emit(
                    "speech_started",
                    user_id=user_id,
                    session_id=session_id,
                    stream_id=getattr(core, "_audio_stream_by_session", {}).get(session_id, ""),
                    reason="provider_speech_started",
                    output_stream_id=active_stream_id,
                    state=state,
                    has_active_output=has_active_output,
                    interruptible_state=interruptible_state,
                    interruptible=interruptible,
                    will_cancel=interruptible,
                    provider_event=event,
                )
                emitter.emit(
                    "output_cancel_requested",
                    user_id=user_id,
                    session_id=session_id,
                    stream_id=active_stream_id or "",
                    reason="provider_speech_started",
                    output_stream_id=active_stream_id,
                    state=state,
                    has_active_output=has_active_output,
                    interruptible_state=interruptible_state,
                    interruptible=interruptible,
                )
            elif event == "omni.input_audio_buffer.speech_stopped":
                emitter.emit(
                    "speech_stopped",
                    user_id=user_id,
                    session_id=session_id,
                    stream_id=getattr(core, "_audio_stream_by_session", {}).get(session_id, ""),
                    reason="provider_speech_stopped",
                    provider_event=event,
                )
            original(user_id=user_id, session_id=session_id, record=record)

        self.core._record_provider_event = _record_provider_event
        setattr(self.core, "_omni_pipeline_boundary_bridge_installed", True)

    def attach_upstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定上行麦克风连接，并确保 Omni provider session 已准备好。"""

        self._upstreams[stream_ref.stream_id] = stream_ref
        self.core.open(stream_ref.user_id, stream_ref.session_id)
        return self.emitter.emit(
            "upstream_ready",
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
        )

    def append_audio(self, chunk: StreamChunk) -> None:
        """向 Omni provider 输入路径持续写入麦克风音频。"""

        self.core.append_audio_event(chunk)

    def detach_upstream(self, stream_ref: StreamRef, *, reason: str) -> PipelineEvent:
        """解绑上行麦克风连接。"""

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


class OmniResponseEngine:
    """Omni 链路回复引擎。

    主要功能：管理 Omni provider session 的回复侧事件，把 provider 音频输出转换成
    统一 pipeline `output_audio_delta/output_finished` 事件。
    主要方法：`prepare_session()`、`attach_downstream()`、`cancel_current_response()`、
    `prepare_close()` 和 `close()`。
    """

    def __init__(self, *, core: OmniAgentCore, output_controller: RealtimeOutputController, emitter: PipelineEventEmitter) -> None:
        self.core = core
        self.output_controller = output_controller
        self.emitter = emitter
        self._install_output_bridge()

    def _install_output_bridge(self) -> None:
        """安装 Omni provider 音频输出到 PipelineEvent 的转换桥。"""

        if getattr(self.core, "_omni_pipeline_output_bridge_installed", False):
            return
        original_delta = self.core.output_adapter.emit_audio_delta
        original_done = self.core.output_adapter.emit_audio_done
        emitter = self.emitter

        def _emit_audio_delta(*, user_id: str, session_id: str, audio: bytes, format, metadata: dict[str, Any]) -> None:
            emitter.emit(
                "output_audio_delta",
                user_id=user_id,
                session_id=session_id,
                payload_size=len(audio),
                format=getattr(format, "__dict__", {}),
                provider=metadata.get("provider"),
                model=metadata.get("model"),
            )
            original_delta(user_id=user_id, session_id=session_id, audio=audio, format=format, metadata=metadata)

        def _emit_audio_done(*, user_id: str, session_id: str, metadata: dict[str, Any]) -> None:
            original_done(user_id=user_id, session_id=session_id, metadata=metadata)
            emitter.emit(
                "output_finished",
                user_id=user_id,
                session_id=session_id,
                provider=metadata.get("provider"),
                model=metadata.get("model"),
                reason=metadata.get("reason") or "assistant_audio.done",
            )

        self.core.output_adapter.emit_audio_delta = _emit_audio_delta
        self.core.output_adapter.emit_audio_done = _emit_audio_done
        setattr(self.core, "_omni_pipeline_output_bridge_installed", True)

    def prepare_session(self, *, user_id: str, session_id: str) -> PipelineEvent:
        """记录 Omni 回复引擎已准备好。"""

        return self.emitter.emit("response_engine_ready", user_id=user_id, session_id=session_id)

    def attach_downstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定下行扬声器 stream。"""

        self.output_controller.bind_downstream(
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
            reason="pipeline_attach_downstream",
            prepare_vision_output=False,
        )
        return self.emitter.emit(
            "downstream_ready",
            user_id=stream_ref.user_id,
            session_id=stream_ref.session_id,
            stream_id=stream_ref.stream_id,
        )

    def cancel_current_response(self, *, user_id: str, reason: str) -> None:
        """取消当前 Omni provider response。"""

        self.core.interrupt(user_id, reason=reason)

    def prepare_close(self, *, user_id: str, session_id: str, reason: str) -> PipelineEvent:
        """准备关闭连续对话，停止接收新的输出。"""

        self.output_controller.stop_accepting_new_output(session_id=session_id, reason=reason)
        return self.emitter.emit("close_prepared", user_id=user_id, session_id=session_id, reason=reason)

    def close(self, *, user_id: str, reason: str) -> None:
        """关闭 Omni provider session。"""

        self.core.close(user_id, reason=reason)


class OmniRealtimePipeline:
    """Omni realtime 音频对话 pipeline。

    主要功能：实现 `RealtimeAgentRealtimePipeline` 设计中的 Omni 链路，把 Omni provider
    的 VAD、ASR、LLM、TTS 细节封装在 pipeline 内部，对外只暴露统一事件和统一接口。
    主要属性：`core/input_boundary/response_engine/output_controller/emitter` 分别对应
    设计时序图中的真实组件。
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
        core: OmniAgentCore,
        output_controller: RealtimeOutputController,
        recorder: RunRecorder,
        normalizer: RealtimeAudioNormalizer | None = None,
    ) -> None:
        object.__setattr__(self, "core", core)
        emitter = PipelineEventEmitter(recorder=recorder)
        object.__setattr__(self, "emitter", emitter)
        object.__setattr__(self, "output_controller", output_controller)
        object.__setattr__(self, "normalizer", normalizer or RealtimeAudioNormalizer())
        object.__setattr__(
            self,
            "input_boundary",
            OmniInputBoundary(core=core, emitter=emitter, output_controller=output_controller),
        )
        object.__setattr__(
            self,
            "response_engine",
            OmniResponseEngine(core=core, output_controller=output_controller, emitter=emitter),
        )
        object.__setattr__(self, "_session_by_user", {})
        object.__setattr__(self, "_downstream_by_session", {})

    def __getattr__(self, name: str) -> Any:
        """把旧调用点透明代理到内部 OmniAgentCore。"""

        return getattr(self.core, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """把旧 OmniAgentCore 属性写入代理到内部 core。"""

        if name in self._LOCAL_ATTRS:
            object.__setattr__(self, name, value)
            return
        setattr(self.core, name, value)

    def bind_pipeline_event_handler(self, handler) -> None:
        """绑定 RealtimeAgentApp 的 PipelineEvent 控制面处理器。"""

        self.emitter.add_listener(handler)

    def open_session(self, user_id: str, session_id: str) -> PipelineEvent:
        """打开 Omni realtime 连续对话 session。"""

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
        """追加上行音频。"""

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
        """关闭 Omni realtime 连续对话 session。"""

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
        """返回内部 OmniAgentCore 事件快照。"""

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

    def bind_tool_gateway(self, tool_gateway: ToolGateway) -> None:
        """绑定工具网关。"""

        self.core.bind_tool_gateway(tool_gateway)

    def bind_user_activity_callback(self, callback: Callable[[str, str], None]) -> None:
        """绑定有效用户语音活动回调。"""

        self.core.bind_user_activity_callback(callback)


def create_omni_realtime_pipeline(
    *,
    output_service: OutputService,
    recorder: RunRecorder,
    control_service: ControlService | None = None,
    asset_service: AssetService | None = None,
    omni_config: RealtimeProviderConfig | None = None,
    provider_factory=None,
    tool_gateway: ToolGateway | None = None,
    memory_service: Any = None,
    max_context_messages: int = 30,
) -> OmniRealtimePipeline:
    """创建 Omni realtime pipeline。

    主要逻辑：集中构造设计文档中的 `OmniAgentCore`、`RealtimeOutputController`
    和 `OmniRealtimePipeline`，避免 router 直接拼装内部组件。
    """

    core = OmniAgentCore(
        output_service=output_service,
        recorder=recorder,
        control_service=control_service,
        asset_service=asset_service,
        omni_config=omni_config,
        provider_factory=provider_factory,
        tool_gateway=tool_gateway,
        memory_service=memory_service,
        max_context_messages=max_context_messages,
    )
    return OmniRealtimePipeline(
        core=core,
        output_controller=RealtimeOutputController(output_service=output_service, recorder=recorder),
        recorder=recorder,
    )
