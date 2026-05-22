from __future__ import annotations

from contextlib import suppress
from typing import Any

from realtime_agent.agent_core.base import AgentEventBuffer
from realtime_agent.protocol import SERVER_PRODUCER_ID, Event

DEFAULT_RECOVERABLE_ERROR_MESSAGE = "抱歉，刚才处理时出错了。你可以再说一遍，我会继续处理。"


def record_agent_recovery_error(
    *,
    recorder: Any,
    event_buffer: AgentEventBuffer,
    user_id: str,
    session_id: str,
    component: str,
    error: Exception | None = None,
    message: str | None = None,
    control_service: Any = None,
    stream_id: str | None = None,
    stream_type: str | None = None,
    agent_event: str = "response.failed",
    recoverable: bool = True,
    fallback_text: str = DEFAULT_RECOVERABLE_ERROR_MESSAGE,
    record: dict[str, Any] | None = None,
    publish_control_event: bool = True,
) -> Event:
    """记录 Agent Core 可恢复异常并按统一协议下发错误事件。

    主要逻辑：
    1. 构造 `system.error.raised` 控制事件，写入 session 事件和系统事件。
    2. 写入统一 Agent 事件，例如 `response.failed` 或 `session.error`。
    3. 可选通过 ControlService 下发给端侧，让端侧有机会展示或提示错误。

    参数：
    1. `recorder`：RunRecorder 或兼容对象。
    2. `event_buffer`：Agent Core 内存事件缓存。
    3. `user_id/session_id`：错误关联的用户和会话。
    4. `component`：出错组件名称。
    5. `error/message`：原始异常或错误文本，二者至少应提供一个。
    6. `control_service`：可选控制服务；提供时会发布错误事件。
    7. `stream_id/stream_type`：可选输入流信息。
    8. `agent_event`：统一 Agent 事件名。
    9. `recoverable/fallback_text`：恢复语义和用户兜底提示。
    10. `record`：附加结构化字段。
    11. `publish_control_event`：是否向端侧发布控制事件。

    返回值：已构造的 `system.error.raised` 事件。
    异常情况：记录和下发失败会被吞掉，避免异常恢复逻辑再次打断热路径。
    """

    detail = dict(record or {})
    if error is not None:
        error_type = type(error).__name__
        error_message = str(error)
    else:
        error_type = str(detail.pop("error_type", "") or "AgentCoreError")
        error_message = str(message or detail.pop("message", "") or "Agent core failed")
    payload = {
        **detail,
        "message": error_message,
        "error_type": error_type,
        "component": component,
        "recoverable": bool(recoverable),
        "fallback_text": fallback_text,
    }
    error_event = Event(
        event_name="system.error.raised",
        user_id=user_id,
        producer_id=SERVER_PRODUCER_ID,
        session_id=session_id,
        stream_id=stream_id,
        stream_type=stream_type,
        payload=payload,
    )
    with suppress(Exception):
        recorder.record_event(error_event)
    with suppress(Exception):
        recorder.record_system_event(error_event.to_dict())
    if publish_control_event and control_service is not None:
        with suppress(Exception):
            control_service.publish(error_event)
    agent_payload = {
        **payload,
        "system_event": "system.error.raised",
    }
    with suppress(Exception):
        recorder.record_agent_event(session_id or "agent-recovery", {"event": agent_event, "user_id": user_id, **agent_payload})
    event_buffer.record_event(agent_event, user_id=user_id, session_id=session_id, payload=agent_payload)
    return error_event
