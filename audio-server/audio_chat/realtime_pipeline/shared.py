from __future__ import annotations

from typing import Callable

from audio_chat.audio_pipeline import AudioPipelineConfig, AudioProcessor, AudioProcessorResult, FormatValidator, Pcm16Resampler, VolumeProbe
from audio_chat.observability import RunRecorder
from audio_chat.output import OutputService
from audio_chat.protocol import StreamChunk
from audio_chat.realtime_pipeline.base import PipelineEvent


class PipelineEventEmitter:
    """Pipeline 统一事件输出器。

    主要功能：保存并记录 Text / Omni pipeline 对外输出的稳定事件。
    主要方法：`emit()` 生成 `PipelineEvent` 并写入 runs；`events()` 返回快照。
    主要属性：`recorder` 用于运行产物记录。
    """

    def __init__(self, *, recorder: RunRecorder) -> None:
        self.recorder = recorder
        self._events: list[PipelineEvent] = []
        self._listeners: list[Callable[[PipelineEvent], None]] = []

    def add_listener(self, listener: Callable[[PipelineEvent], None]) -> None:
        """注册 pipeline 事件监听器。

        主要逻辑：AudioChatApp 通过监听器消费稳定的 PipelineEvent，把原先散落在
        Text/Omni core 内部的控制动作逐步收敛到统一控制面。
        参数：`listener` 接收一个 PipelineEvent。
        返回值：无。
        异常情况：监听器异常会被记录为 system event，不中断音频热路径。
        """

        self._listeners.append(listener)

    def emit(
        self,
        event: str,
        *,
        user_id: str = "",
        session_id: str = "",
        stream_id: str = "",
        record: bool = True,
        **payload,
    ) -> PipelineEvent:
        """生成并记录一个 pipeline 事件。

        参数：`event` 为统一事件名；`record` 控制是否写入 runs 产物；
        `payload` 为事件详情。
        返回值：生成的 `PipelineEvent`。
        异常情况：无。
        """

        item = PipelineEvent(event=event, user_id=user_id, session_id=session_id, stream_id=stream_id, payload=dict(payload))
        self._events.append(item)
        if record and session_id:
            self.recorder.record_agent_event(
                session_id,
                {
                    "event": f"pipeline.{event}",
                    "user_id": user_id,
                    "stream_id": stream_id,
                    **dict(payload),
                },
            )
        for listener in list(self._listeners):
            try:
                listener(item)
            except Exception as exc:  # noqa: BLE001
                self.recorder.record_system_event(
                    {
                        "event": "system.error.raised",
                        "component": "PipelineEventEmitter",
                        "session_id": session_id,
                        "pipeline_event": event,
                        "message": f"{type(exc).__name__}: {exc}",
                        "severity": "warning",
                    }
                )
        return item

    def events(self) -> list[PipelineEvent]:
        """返回已输出的 pipeline 事件快照。"""

        return list(self._events)


class RealtimeAudioNormalizer:
    """Realtime pipeline 内部音频归一化组件。

    主要功能：提供设计文档中的 `RealtimeAudioNormalizer` 真实实现，复用现有格式校验、
    重采样和音量探针；不执行 VAD，也不决定用户 turn boundary。
    主要方法：`process()` 返回归一化后的音频片和诊断信息。
    主要属性：`processors` 是实际执行的音频处理器列表。
    """

    def __init__(self, *, config: AudioPipelineConfig | None = None, processors: list[AudioProcessor] | None = None) -> None:
        self.config = config or AudioPipelineConfig(vad="provider")
        self.processors = processors or self._default_processors()
        self.last_diagnostics: list[dict] = []

    def process(self, chunk: StreamChunk) -> AudioProcessorResult:
        """处理一片上行音频。

        主要逻辑：依次执行格式校验、重采样和音量探针；VAD 必须来自 provider 或
        Text/Omni 输入边界组件，因此这里不会产生 speech_start / speech_stop。
        参数：`chunk` 为麦克风音频片。
        返回值：最后一个处理器的 `AudioProcessorResult`。
        异常情况：格式不支持或重采样失败时抛出异常。
        """

        current = chunk
        diagnostics: list[dict] = []
        result = AudioProcessorResult(chunk=current, diagnostics={})
        for processor in self.processors:
            try:
                result = processor.process(current)
            except Exception as exc:
                if getattr(processor, "name", "") != "volume_probe":
                    raise
                diagnostics.append(
                    {
                        "processor": processor.name,
                        "degraded": True,
                        "reason": "volume_probe_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            current = result.chunk
            diagnostics.append({"processor": processor.name, **dict(result.diagnostics)})
        self.last_diagnostics = diagnostics
        return AudioProcessorResult(chunk=current, diagnostics={"processors": diagnostics})

    def _default_processors(self) -> list[AudioProcessor]:
        processors: list[AudioProcessor] = [FormatValidator(self.config), Pcm16Resampler(self.config)]
        if self.config.volume_probe:
            processors.append(VolumeProbe())
        return processors


class RealtimeOutputController:
    """Realtime pipeline 内部共享输出控制器。

    主要功能：把 pipeline 里的 output finish / cancel / TTS 预热语义落到现有
    `OutputService` 上。它不是 `StreamService` 或 `ControlService` 的替代品。
    主要方法：绑定下行、暂停/恢复、取消活跃输出、停止接收新输出和关闭 Text TTS。
    """

    def __init__(self, *, output_service: OutputService, recorder: RunRecorder) -> None:
        self.output_service = output_service
        self.recorder = recorder
        self._paused_sessions: set[str] = set()
        self._closed_sessions: set[str] = set()
        self._downstream_by_session: dict[str, str] = {}

    def bind_downstream(
        self,
        *,
        user_id: str,
        session_id: str,
        stream_id: str,
        reason: str = "downstream_attached",
        prepare_text_output: bool = True,
    ) -> None:
        """绑定下行扬声器 stream，并按链路需要预热 Text TTS session。"""

        self._downstream_by_session[session_id] = stream_id
        self._closed_sessions.discard(session_id)
        if prepare_text_output:
            self.output_service.prepare_text_session(session_id, reason=reason)
        self.recorder.record_agent_event(
            session_id,
            {"event": "realtime_output.downstream_bound", "user_id": user_id, "stream_id": stream_id, "reason": reason},
        )

    def pause(self, *, user_id: str, session_id: str) -> None:
        """记录端侧下行高水位暂停请求。"""

        self._paused_sessions.add(session_id)
        self.output_service.pause_session(user_id=user_id, session_id=session_id)
        self.recorder.record_agent_event(session_id, {"event": "realtime_output.paused", "user_id": user_id})

    def resume(self, *, user_id: str, session_id: str) -> None:
        """记录端侧下行低水位恢复请求。"""

        self._paused_sessions.discard(session_id)
        self.output_service.resume_session(user_id=user_id, session_id=session_id)
        self.recorder.record_agent_event(session_id, {"event": "realtime_output.resumed", "user_id": user_id})

    def cancel_active_output(self, *, user_id: str, session_id: str, reason: str) -> None:
        """取消当前活跃 output stream。"""

        self.output_service.interrupt_user(user_id, session_id=session_id, reason=reason)

    def stop_accepting_new_output(self, *, session_id: str, reason: str) -> None:
        """标记 session 进入关闭阶段，不再接受新的输出。

        主要逻辑：现阶段由 TextAgentCore 的 generation 语义阻止旧回复继续输出；这里
        记录 pipeline 输出层状态，后续实现可以在此处增加更严格的写入门控。
        """

        self._closed_sessions.add(session_id)
        self.recorder.record_agent_event(session_id, {"event": "realtime_output.stop_accepting_new_output", "reason": reason})

    def close_text_session(self, *, session_id: str, reason: str) -> None:
        """关闭连续对话级 Text TTS provider。"""

        self.output_service.close_text_session(session_id, reason=reason)

    def active_output_stream_id(self, *, user_id: str, session_id: str) -> str | None:
        """返回当前活跃输出 stream id。"""

        return self.output_service.active_output_stream_id(user_id, session_id)


def make_event_callback(emitter: PipelineEventEmitter, event_name: str) -> Callable[..., PipelineEvent]:
    """创建固定事件名的 emit 回调。

    主要功能：减少 pipeline 内部组件之间直接依赖 `PipelineEventEmitter` 的地方。
    """

    def _emit(**kwargs) -> PipelineEvent:
        return emitter.emit(event_name, **kwargs)

    return _emit
