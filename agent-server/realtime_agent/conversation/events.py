from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from realtime_agent.observability import RunRecorder


"""conversation 内部事件名。

主要功能：集中放置新 runtime 的内部事件常量，避免与设备控制事件、provider
原始事件混淆。
"""

SPEECH_INPUT_AUDIO_CHUNK = "speech_input.audio_chunk"
SPEECH_INPUT_ASR_TEXT_DELTA = "speech_input.asr_text_delta"
SPEECH_INPUT_TURN_STARTED = "speech_input.turn_started"
SPEECH_INPUT_TURN_ENDED = "speech_input.turn_ended"


@dataclass(frozen=True)
class ConversationRuntimeEvent:
    """conversation runtime 对 app 层输出的轻量事件。

    主要功能：让 conversation runtime 用稳定结构通知 app 层 speech boundary、
    output cancel、stream ready 等状态，同时不依赖 legacy `realtime_pipeline`。
    主要属性：`event` 是 runtime 内部事件名，`payload` 保存细节。
    """

    event: str
    user_id: str = ""
    session_id: str = ""
    stream_id: str = ""
    payload: dict[str, Any] = field(default_factory=dict)


class ConversationRuntimeEventEmitter:
    """conversation runtime 事件输出器。

    主要功能：记录 conversation 内部事件并通知 app 层监听器；该类只处理事件
    转发和 runs 记录，不承担打断、视觉采样或 provider 提交逻辑。
    主要属性：`recorder` 负责运行产物记录，`_listeners` 保存 app 层回调。
    """

    def __init__(self, *, recorder: RunRecorder) -> None:
        self.recorder = recorder
        self._events: list[ConversationRuntimeEvent] = []
        self._listeners: list[Callable[[ConversationRuntimeEvent], None]] = []

    def add_listener(self, listener: Callable[[ConversationRuntimeEvent], None]) -> None:
        """注册事件监听器。

        参数：`listener` 接收一个 `ConversationRuntimeEvent`。
        返回值：无。
        异常情况：监听器异常会写入 system event，不中断音频热路径。
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
    ) -> ConversationRuntimeEvent:
        """生成并记录一个 conversation runtime 事件。

        参数：`event` 为内部事件名；`record` 控制是否写入 runs；
        `payload` 为事件细节。
        返回值：生成的 `ConversationRuntimeEvent`。
        异常情况：无。
        """

        item = ConversationRuntimeEvent(event=event, user_id=user_id, session_id=session_id, stream_id=stream_id, payload=dict(payload))
        self._events.append(item)
        if record and session_id:
            self.recorder.record_conversation_event(
                session_id,
                {
                    "event": f"conversation.{event}",
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
                        "component": "ConversationRuntimeEventEmitter",
                        "session_id": session_id,
                        "conversation_event": event,
                        "message": f"{type(exc).__name__}: {exc}",
                        "severity": "warning",
                    }
                )
        return item

    def events(self) -> list[ConversationRuntimeEvent]:
        """返回已输出的事件快照。"""

        return list(self._events)
