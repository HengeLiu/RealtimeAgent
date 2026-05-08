"""任务事件回流桥。"""

from __future__ import annotations

from agent_core.context import AgentSessionStore, AgentTurn, MessageContext, generate_id
from agent_core.context.models import DerivedArtifact, TaskRef
from backend_task_core import TaskEvent
from runtime.notifications import NotificationRequest


class TaskEventBridge:
    """把任务事件同步到会话并转换成通知申请。

    主要功能：
    1. 把 `TaskEvent` 写入会话上下文。
    2. 在允许直发时生成 `NotificationRequest`。
    """

    def __init__(self, *, session_store: AgentSessionStore) -> None:
        self._session_store = session_store

    def handle_event(self, event: TaskEvent) -> NotificationRequest | None:
        """处理单条任务事件。

        返回值：
        1. 若当前事件允许通知，则返回对应通知申请。
        2. 否则返回 `None`。
        """

        message_text = str(event.payload.get("message", event.event_name))
        self._session_store.get_or_create_session(session_id=event.session_id, device_id=event.device_id)
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=event.session_id,
            artifact_type="task_event",
            storage_uri=f"memory://task/{event.task_id}/event/{event.event_id}",
            text=message_text,
            meta={
                "event_id": event.event_id,
                "event_name": event.event_name,
                "task_id": event.task_id,
                "task_type": event.task_type,
                "state": event.state,
                "priority": event.priority,
                "payload": dict(event.payload),
            },
        )
        task_ref = TaskRef(
            task_id=event.task_id,
            task_type=event.task_type,
            state=event.state,
            summary=message_text,
        )
        artifact_ids = self._session_store.save_artifacts(session_id=event.session_id, artifacts=[artifact])
        task_ids = self._session_store.save_task_refs(session_id=event.session_id, task_refs=[task_ref])
        self._session_store.append_message(
            session_id=event.session_id,
            message=MessageContext(
                message_id=generate_id("msg"),
                session_id=event.session_id,
                role="assistant",
                kind="task_notification",
                text=message_text,
                derived_refs=artifact_ids,
                task_refs=task_ids,
                meta={
                    "source": "backend_task_core",
                    "event_id": event.event_id,
                    "event_name": event.event_name,
                },
            ),
        )

        if not event.allow_direct_notify or not message_text.strip():
            return None

        return NotificationRequest(
            request_id=generate_id("notify_req"),
            source_module="backend-task-core",
            session_id=event.session_id,
            device_id=event.device_id,
            task_id=event.task_id,
            priority=event.priority,
            notification_type=event.event_name,
            delivery_mode="audio",
            allow_interrupt=event.priority in {"high", "critical"},
            allow_merge=event.priority in {"low", "normal"},
            requires_agent_context_sync=event.requires_agent_decision,
            dedupe_key=f"{event.event_name}:{event.task_id}",
            payload={
                "text": message_text,
                "task_type": event.task_type,
                "task_state": event.state,
                **dict(event.payload),
            },
        )

    def convert_event_to_agent_turn(self, event: TaskEvent) -> AgentTurn:
        """把任务事件转换成 `AgentTurn`。

        主要逻辑：
        1. 把结构化任务事件转换成一轮 `task_event` 输入。
        2. 保留任务引用和事件摘要，供 `agent-core` 做后续决策。
        """

        message_text = str(event.payload.get("message", event.event_name))
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=event.session_id,
            artifact_type="task_event_turn_input",
            storage_uri=f"memory://task/{event.task_id}/turn/{event.event_id}",
            text=message_text,
            meta={
                "event_id": event.event_id,
                "event_name": event.event_name,
                "task_id": event.task_id,
                "task_type": event.task_type,
                "state": event.state,
                "priority": event.priority,
                "payload": dict(event.payload),
            },
        )
        return AgentTurn(
            turn_id=generate_id("turn"),
            session_id=event.session_id,
            device_id=event.device_id,
            source="task_event",
            input_text=(
                "后台任务触发了一条事件，请结合当前会话决定下一步回复。\n"
                f"任务类型：{event.task_type}\n"
                f"任务编号：{event.task_id}\n"
                f"事件类型：{event.event_name}\n"
                f"任务状态：{event.state}\n"
                f"事件内容：{message_text}"
            ),
            derived_artifacts=[artifact],
            meta={
                "task_id": event.task_id,
                "task_type": event.task_type,
                "task_event_name": event.event_name,
                "task_event_priority": event.priority,
            },
        )
