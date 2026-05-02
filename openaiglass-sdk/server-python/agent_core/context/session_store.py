"""agent-core 会话上下文存储。"""

from __future__ import annotations

import threading
from typing import Iterable

from agent_core.context.models import AgentSession, CapabilityTrace, DerivedArtifact, MediaAssetRef, MessageContext, TaskRef, now_ms


class AgentSessionStore:
    """最小会话上下文存储。

    主要功能：
    1. 维护 `session_id -> AgentSession` 索引。
    2. 提供消息、资产、派生结果和轨迹的线程安全写入能力。
    3. 为 `voice-runtime` 和 `agent-core` 提供统一上下文访问入口。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, AgentSession] = {}

    def get_or_create_session(self, *, session_id: str, device_id: str) -> AgentSession:
        """获取或创建会话。

        参数：
        1. `session_id`：会话编号。
        2. `device_id`：设备编号。

        返回值：
        1. 对应的 `AgentSession`。
        """

        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                session = AgentSession(session_id=session_id, device_id=device_id)
                self._sessions[session_id] = session
            else:
                session.device_id = device_id
                session.updated_at_ms = now_ms()
            return session

    def get_session(self, session_id: str) -> AgentSession | None:
        """按编号查询会话。

        参数：
        1. `session_id`：会话编号。

        返回值：
        1. 命中时返回 `AgentSession`，否则返回 `None`。
        """

        with self._lock:
            return self._sessions.get(session_id)

    def save_assets(self, *, session_id: str, assets: Iterable[MediaAssetRef]) -> list[str]:
        """批量保存媒体资产引用。

        参数：
        1. `session_id`：会话编号。
        2. `assets`：待保存的媒体资产列表。

        返回值：
        1. 被写入的 `asset_id` 列表。
        """

        with self._lock:
            session = self._sessions[session_id]
            saved_ids: list[str] = []
            for asset in assets:
                session.assets[asset.asset_id] = asset
                saved_ids.append(asset.asset_id)
            session.updated_at_ms = now_ms()
            return saved_ids

    def save_artifacts(self, *, session_id: str, artifacts: Iterable[DerivedArtifact]) -> list[str]:
        """批量保存派生结果引用。

        参数：
        1. `session_id`：会话编号。
        2. `artifacts`：待保存的派生结果列表。

        返回值：
        1. 被写入的 `artifact_id` 列表。
        """

        with self._lock:
            session = self._sessions[session_id]
            saved_ids: list[str] = []
            for artifact in artifacts:
                session.artifacts[artifact.artifact_id] = artifact
                saved_ids.append(artifact.artifact_id)
            session.updated_at_ms = now_ms()
            return saved_ids

    def append_message(self, *, session_id: str, message: MessageContext) -> None:
        """追加一条会话消息。

        参数：
        1. `session_id`：会话编号。
        2. `message`：待追加的消息对象。
        """

        with self._lock:
            session = self._sessions[session_id]
            session.messages.append(message)
            session.updated_at_ms = now_ms()

    def attach_assets_to_message(self, *, session_id: str, message_id: str, asset_ids: list[str]) -> None:
        """把资产引用追加到指定消息。

        参数：
        1. `session_id`：会话编号。
        2. `message_id`：目标消息编号。
        3. `asset_ids`：待挂接的资产编号列表。
        """

        with self._lock:
            session = self._sessions[session_id]
            for message in session.messages:
                if message.message_id != message_id:
                    continue
                for asset_id in asset_ids:
                    if asset_id not in message.asset_refs:
                        message.asset_refs.append(asset_id)
                session.updated_at_ms = now_ms()
                return

    def update_message_text(self, *, session_id: str, message_id: str, text: str) -> None:
        """更新指定消息的文本内容。

        功能：
            用于原生音频模型先占位写入用户消息，再在模型返回转写文本后回填真实文本。

        主要逻辑：
            在指定会话中按 `message_id` 查找消息，命中后替换 `text` 并更新会话时间戳。

        参数：
            session_id: 会话编号。
            message_id: 要更新的消息编号。
            text: 新的消息文本。

        返回值：
            无。

        异常情况：
            当 `session_id` 不存在时会沿用字典访问行为抛出 `KeyError`；未找到消息时不做修改。
        """

        with self._lock:
            session = self._sessions[session_id]
            for message in session.messages:
                if message.message_id != message_id:
                    continue
                message.text = text
                session.updated_at_ms = now_ms()
                return

    def append_capability_traces(self, *, session_id: str, traces: Iterable[CapabilityTrace]) -> None:
        """追加能力调用轨迹。

        参数：
        1. `session_id`：会话编号。
        2. `traces`：待追加的轨迹列表。
        """

        with self._lock:
            session = self._sessions[session_id]
            session.capability_traces.extend(traces)
            session.updated_at_ms = now_ms()

    def save_task_refs(self, *, session_id: str, task_refs: Iterable[TaskRef]) -> list[str]:
        """批量保存任务引用。

        参数：
        1. `session_id`：会话编号。
        2. `task_refs`：待保存的任务引用列表。

        返回值：
        1. 被写入的 `task_id` 列表。
        """

        with self._lock:
            session = self._sessions[session_id]
            saved_ids: list[str] = []
            for task_ref in task_refs:
                session.tasks[task_ref.task_id] = task_ref
                saved_ids.append(task_ref.task_id)
            session.updated_at_ms = now_ms()
            return saved_ids
