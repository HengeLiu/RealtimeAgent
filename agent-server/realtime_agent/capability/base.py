from __future__ import annotations

from typing import Any, Mapping, Protocol


class ToolGatewayABC(Protocol):
    """Tool Gateway 抽象。

    主要功能：注册工具、生成 provider function schema、执行参数校验，并通过
    ToolContext 调用业务工具。
    """

    def tool_schemas(self) -> list[Mapping[str, Any]]:
        """返回模型可见工具 schema。"""

    def provider_schemas(self) -> list[Mapping[str, Any]]:
        """返回 provider function calling 可消费的工具 schema。"""

    def call_tool(self, *, name: str, arguments: Mapping[str, Any], context: Any) -> Any:
        """调用一个工具并返回工具结果。"""


class TaskEngineABC(Protocol):
    """Task Engine 抽象。

    主要功能：启动、取消和查询长生命周期任务，并把 TaskSignal 回流到 Agent 或
    Output 层。
    """

    def start_task(self, *, name: str, parameters: Mapping[str, Any], context: Any) -> Any:
        """启动一个任务。"""

    def cancel_task(self, *, task_id: str, reason: str) -> None:
        """取消一个任务。"""

    def query_task(self, *, task_id: str) -> Any:
        """查询任务状态。"""

    def handle_command_result(self, *, command_id: str, result: Mapping[str, Any]) -> None:
        """接收端侧 command 回报并推进对应任务。"""

    def emit_signal(self, *, task_id: str, signal: Any) -> None:
        """把 TaskSignal 回流给 Agent 或 Output 层。"""


class SkillGatewayABC(Protocol):
    """Skill Gateway 抽象。

    主要功能：发现和调用 skill 能力，并通过 ToolGateway 或 Context API 暴露给
    Agent。
    """

    def list_skills(self) -> list[Mapping[str, Any]]:
        """列出可用 skill。"""


class McpGatewayABC(Protocol):
    """MCP Gateway 抽象。

    主要功能：管理外部 MCP 工具或资源能力，并以稳定 schema 进入能力层。
    """

    def list_tools(self) -> list[Mapping[str, Any]]:
        """列出 MCP 工具。"""
