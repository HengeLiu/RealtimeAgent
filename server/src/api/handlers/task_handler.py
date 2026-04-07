from __future__ import annotations

import logging

from backend_task_core.manager.task_manager import TaskManager
from infra.clock.system_clock import SystemClock
from infra.logging import log_event
from protocol.enums import MessageType, Priority, TaskSource
from protocol.messages.envelope import Endpoint, Envelope


class TaskHandler:
    def __init__(self, *, task_manager: TaskManager, logger: logging.Logger) -> None:
        self._task_manager = task_manager
        self._logger = logger

    def handle(self, envelope: Envelope) -> list[Envelope]:
        try:
            if envelope.message_name == "task.create":
                task_type = envelope.payload.get("task_type")
                if not task_type:
                    return [self._error(envelope, "validation_error", "task_type is required")]
                task = self._task_manager.create_task(
                    task_type=str(task_type),
                    source=TaskSource(envelope.payload.get("source", TaskSource.AGENT.value)),
                    priority=Priority(envelope.payload.get("priority", Priority.NORMAL.value)),
                    input_data=dict(envelope.payload.get("input") or {}),
                )
                log_event(
                    self._logger,
                    logging.INFO,
                    "task.create.accepted",
                    trace_id=envelope.trace_id,
                    message_id=envelope.message_id,
                    task_id=task.task_id,
                )
                return [self._event(envelope, "task.created", task.to_dict())]

            task_id = str(envelope.payload.get("task_id", ""))
            if not task_id:
                return [self._error(envelope, "validation_error", "task_id is required")]

            if envelope.message_name == "task.query":
                task = self._task_manager.get(task_id)
                if not task:
                    return [self._error(envelope, "task_not_found", f"unknown task_id={task_id}")]
                log_event(
                    self._logger,
                    logging.DEBUG,
                    "task.query.hit",
                    trace_id=envelope.trace_id,
                    message_id=envelope.message_id,
                    task_id=task_id,
                )
                return [self._event(envelope, "task.state", task.to_dict())]

            if envelope.message_name == "task.pause":
                task = self._task_manager.pause(task_id)
                log_event(self._logger, logging.INFO, "task.paused", trace_id=envelope.trace_id, task_id=task_id)
                return [self._event(envelope, "task.paused", task.to_dict())]

            if envelope.message_name == "task.resume":
                task = self._task_manager.resume(task_id)
                log_event(self._logger, logging.INFO, "task.resumed", trace_id=envelope.trace_id, task_id=task_id)
                return [self._event(envelope, "task.state", task.to_dict())]

            if envelope.message_name == "task.cancel":
                task = self._task_manager.cancel(task_id)
                log_event(self._logger, logging.INFO, "task.cancelled", trace_id=envelope.trace_id, task_id=task_id)
                return [self._event(envelope, "task.cancelled", task.to_dict())]

            if envelope.message_name == "task.start":
                started = self._task_manager.start(task_id)
                return [self._event(envelope, "task.state", started.to_dict())]

            if envelope.message_name == "task.next":
                started = self._task_manager.start_next()
                if not started:
                    return [self._event(envelope, "task.state", {"status": "idle"})]
                return [self._event(envelope, "task.state", started.to_dict())]

            return []
        except KeyError as exc:
            return [self._error(envelope, "task_not_found", str(exc))]
        except ValueError as exc:
            return [self._error(envelope, "task_transition_invalid", str(exc))]

    def _event(self, envelope: Envelope, message_name: str, payload: dict[str, object]) -> Envelope:
        return Envelope(
            message_id=f"{envelope.message_id}_{message_name.split('.')[-1]}",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.EVENT,
            message_name=message_name,
            protocol_version=envelope.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="backend-task-core"),
            target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload=payload,
        )

    def _error(self, envelope: Envelope, code: str, message: str) -> Envelope:
        return Envelope(
            message_id=f"{envelope.message_id}_error",
            trace_id=envelope.trace_id,
            correlation_id=envelope.message_id,
            message_type=MessageType.ERROR,
            message_name="system.error",
            protocol_version=envelope.protocol_version,
            source=Endpoint(device_id=envelope.target.device_id, module="backend-task-core"),
            target=Endpoint(device_id=envelope.source.device_id, module=envelope.source.module),
            timestamp=SystemClock.now_iso(),
            payload={
                "error_code": code,
                "error_message": message,
                "error_type": "validation_error",
                "source": "task_handler",
                "retryable": True,
            },
        )
