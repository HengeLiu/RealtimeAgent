from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from audio_chat.protocol import Event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RemoteCommand:
    """端侧远程命令描述。

    主要功能：从 `command.requested` 控制事件中提取端侧任务回报所需的稳定字段。
    主要属性：`command_id` 标识一次远程命令，`command` 是命令名，`params` 是业务参数。
    """

    command_id: str
    command: str
    user_id: str
    session_id: str
    params: dict[str, Any]

    @classmethod
    def from_event(cls, event: Event) -> "RemoteCommand":
        """从控制事件解析远程命令。

        主要逻辑：读取 payload.command_id、payload.command 和 payload.params，并保留
        user/session 身份。参数缺失时抛出 ValueError，避免端侧回报无 command_id 事件。
        参数：`event` 为 server 下发的 `command.requested`。
        返回值：`RemoteCommand`。
        异常情况：缺少 command_id 或 command 时抛出 ValueError。
        """

        payload = dict(event.payload or {})
        command_id = str(payload.get("command_id") or "").strip()
        command = str(payload.get("command") or "").strip()
        if not command_id:
            raise ValueError("remote command command_id is required")
        if not command:
            raise ValueError("remote command command is required")
        return cls(
            command_id=command_id,
            command=command,
            user_id=event.user_id,
            session_id=event.session_id or event.producer_id or "",
            params=dict(payload.get("params") or {}),
        )


class RemoteTaskReporter:
    """端侧远程任务状态回报 helper。

    主要功能：封装 command.accepted/progress/completed/failed 控制事件发送、公共
    payload 字段和结构化日志。端侧 handler 只需要填写状态名、业务数据和结果。
    主要方法：`accepted()`、`progress()`、`completed()`、`failed()`。
    """

    def __init__(
        self,
        *,
        command: RemoteCommand,
        producer_id: str,
        role: str,
        send_event: Callable[[Event], Awaitable[None]],
    ) -> None:
        self.command = command
        self.producer_id = producer_id
        self.role = role
        self._send_event = send_event

    async def accepted(self, *, message: str = "", data: dict[str, Any] | None = None) -> None:
        """发送 command.accepted。

        参数：`message` 为端侧可读说明，`data` 为附加业务数据。
        返回值：无。
        异常情况：payload 不可 JSON 序列化时抛出 ValueError。
        """

        await self._emit("command.accepted", message=message, data=data)

    async def progress(
        self,
        status: str,
        *,
        message: str = "",
        data: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """发送 command.progress。

        参数：`status` 是业务子状态名，不能为空；`data/metrics` 为业务数据和指标。
        返回值：无。
        异常情况：status 为空或 payload 不可 JSON 序列化时抛出 ValueError。
        """

        normalized = str(status or "").strip()
        if not normalized:
            raise ValueError("remote task progress status is required")
        await self._emit("command.progress", status=normalized, message=message, data=data, metrics=metrics)

    async def completed(self, *, result: dict[str, Any], message: str = "") -> None:
        """发送 command.completed。

        参数：`result` 为端侧远程任务最终结果；`message` 为可读摘要。
        返回值：无。
        异常情况：payload 不可 JSON 序列化时抛出 ValueError。
        """

        await self._emit("command.completed", message=message, result=dict(result or {}))

    async def failed(self, *, message: str, error_code: str = "remote_task_failed", data: dict[str, Any] | None = None) -> None:
        """发送 command.failed。

        参数：`message` 为失败原因；`error_code` 为机器可读错误码；`data` 为附加诊断。
        返回值：无。
        异常情况：payload 不可 JSON 序列化时抛出 ValueError。
        """

        await self._emit("command.failed", message=message, error_code=error_code, data=data)

    def _base_payload(self) -> dict[str, Any]:
        params = dict(self.command.params or {})
        return {
            "command_id": self.command.command_id,
            "command": self.command.command,
            "peer_session_id": str(params.get("peer_session_id") or params.get("task_id") or ""),
            "task_type": str(params.get("task_type") or ""),
            "role": self.role,
        }

    async def _emit(self, event_name: str, **updates: Any) -> None:
        payload = self._base_payload()
        payload.update({key: value for key, value in updates.items() if value is not None})
        try:
            json.dumps(payload, ensure_ascii=False)
        except TypeError as exc:
            raise ValueError(f"remote task payload is not json serializable: {exc}") from exc
        logger.info(
            "remote_task.%s command_id=%s peer_session_id=%s status=%s",
            event_name.rsplit(".", 1)[-1],
            payload.get("command_id"),
            payload.get("peer_session_id"),
            payload.get("status", ""),
        )
        await self._send_event(
            Event(
                event_name=event_name,
                user_id=self.command.user_id,
                producer_id=self.producer_id,
                session_id=self.producer_id,
                payload=payload,
            )
        )
