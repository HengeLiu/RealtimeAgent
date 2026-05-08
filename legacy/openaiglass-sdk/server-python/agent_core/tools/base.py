"""Tool 层基础定义。"""

from __future__ import annotations

import random
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from agent_core.context.models import CapabilityTrace, DerivedArtifact, MediaAssetRef, TaskRef
from agent_core.models import CapabilityResult, ProgressMessage, ToolSpec, normalize_progress_messages
from infra.config import ServerSettings

if TYPE_CHECKING:
    from agent_core.camera import CameraGateway
    from agent_core.camera import UtterancePhotoStore
    from agent_core.context import AgentSessionStore
    from agent_core.memory import AgentMemoryRuntime
    from agent_core.mcp import McpGateway
    from agent_core.tools.gateway import ToolGateway
    from backend_task_core import TaskGateway


@dataclass(slots=True)
class AgentToolContext:
    """能力调用上下文。"""

    session_id: str
    device_id: str
    turn_id: str
    settings: ServerSettings
    session_store: "AgentSessionStore | None"
    device_state_reader: Callable[[], dict[str, Any]]
    trace_sink: Callable[[CapabilityTrace], None]
    device_group_context_factory: Callable[..., Any] | None = None
    task_gateway: "TaskGateway | None" = None
    camera_gateway: "CameraGateway | None" = None
    utterance_photo_store: "UtterancePhotoStore | None" = None
    tool_gateway: "ToolGateway | None" = None
    mcp_gateway: "McpGateway | None" = None
    memory_runtime: "AgentMemoryRuntime | None" = None
    turn_meta: dict[str, Any] = field(default_factory=dict)
    progress_callback: Callable[[str], None] | None = None
    progress_announced_tools: set[str] = field(default_factory=set)
    progress_first_model_output: str | None = None
    progress_first_model_output_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    emitted_assets: list[MediaAssetRef] = field(default_factory=list)
    emitted_artifacts: list[DerivedArtifact] = field(default_factory=list)
    emitted_tasks: list[TaskRef] = field(default_factory=list)

    def absorb(self, result: CapabilityResult) -> None:
        """吸收能力结果中的引用对象。"""

        self._extend_unique(self.emitted_assets, result.asset_refs, "asset_id")
        self._extend_unique(self.emitted_artifacts, result.derived_artifacts, "artifact_id")
        self._extend_unique(self.emitted_tasks, result.task_refs, "task_id")

    def note_model_output(self, kind: str) -> None:
        """记录本轮模型最先返回的输出类型。

        主要逻辑：
        1. 只接受 `tool_call`、`text`、`audio` 三类首输出。
        2. 只记录第一次出现的类型，后续输出不会覆盖。
        3. 该状态用于决定是否自动播报工具前置提示。

        参数：
            kind: 模型输出类型，`tool_call` 表示首输出为工具调用，`text` 表示文本增量，
                `audio` 表示音频增量。

        返回值：
            无。

        异常情况：
            未知类型会被忽略，避免模型适配器异常事件影响工具执行。
        """

        if kind not in {"tool_call", "text", "audio"}:
            return
        with self.progress_first_model_output_lock:
            if self.progress_first_model_output is None:
                self.progress_first_model_output = kind

    def should_announce_tool_progress(self) -> bool:
        """判断当前工具调用是否需要自动播报前置提示。

        主要逻辑：
        1. 如果模型首输出尚未记录，说明当前工具调用就是首输出，记录为 `tool_call`。
        2. 只有首输出为 `tool_call` 时才允许播报。
        3. 首输出为文本或音频时不播报，避免用户已经听到/看到回复后再插入等待提示。

        返回值：
            `True` 表示允许播报工具前置提示，否则返回 `False`。

        异常情况：
            无。
        """

        with self.progress_first_model_output_lock:
            if self.progress_first_model_output is None:
                self.progress_first_model_output = "tool_call"
            return self.progress_first_model_output == "tool_call"

    def announce_tool_progress(self, *, tool_name: str, message: ProgressMessage | None) -> None:
        """在工具执行前向语音运行时发送一次进度播报。

        主要逻辑：
        1. 只有全局工具前置播报开关开启时才生效。
        2. 只在调用方提供 `progress_callback` 时生效。
        3. 支持单句或多句候选文案；多句候选会随机选择一条。
        4. 只有模型首个输出是工具调用时才播报；首个输出是文本或音频时不播报。
        5. 同一轮同一工具只播报一次，避免多工具循环里重复提示。
        6. 播报失败不影响工具本身执行，避免提示语成为业务阻塞点。

        参数：
        1. `tool_name`：即将执行的工具名称。
        2. `message`：播报文本或候选文本列表，空文本表示不播报。

        异常情况：
        1. 回调异常会被吞掉；正式错误仍由工具执行链路返回。
        """

        messages = normalize_progress_messages(message)
        if not self.settings.tool_progress_audio_enabled:
            return
        if self.progress_callback is None or not messages:
            return
        if not self.should_announce_tool_progress():
            return
        if tool_name in self.progress_announced_tools:
            return
        self.progress_announced_tools.add(tool_name)
        try:
            self.progress_callback(random.choice(messages))
        except Exception:
            return

    @staticmethod
    def _extend_unique(target: list[Any], source: list[Any], id_field: str) -> None:
        existing_ids = {getattr(item, id_field) for item in target}
        for item in source:
            item_id = getattr(item, id_field)
            if item_id in existing_ids:
                continue
            target.append(item)
            existing_ids.add(item_id)


class BaseTool(ABC):
    """所有模型可见 Tool 的基类。"""

    spec: ToolSpec

    @abstractmethod
    def run(self, context: AgentToolContext, input_data) -> CapabilityResult:
        """执行 Tool 逻辑。"""


class BaseMcpTool(BaseTool):
    """MCP Tool 基类。"""


class BaseTaskTool(BaseTool):
    """Task Tool 基类。"""
