"""agent-core 最小工具注册表。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from agents import RunContextWrapper, function_tool

from agent_core.context.models import CapabilityTrace, now_ms
from infra.errors import ErrorCode, build_error


@dataclass(slots=True)
class AgentToolContext:
    """工具调用上下文。

    主要功能：
    1. 向 Tool 暴露当前会话、设备和调用轨迹写入口。
    2. 隔离 Tool 对外部运行时的直接依赖。

    主要属性：
    1. `session_id/device_id/turn_id`：当前调用链路标识。
    2. `device_state_reader`：设备状态读取函数。
    3. `trace_sink`：能力轨迹写入函数。
    """

    session_id: str
    device_id: str
    turn_id: str
    device_state_reader: Callable[[], dict[str, Any]]
    trace_sink: Callable[[CapabilityTrace], None]


@dataclass(slots=True)
class RegisteredTool:
    """注册完成的工具定义。"""

    name: str
    description: str
    sdk_tool: Any
    invoke_handler: Callable[..., Any]


class ToolRegistry:
    """最小工具注册表。

    主要功能：
    1. 管理首批 Tool 的注册与发现。
    2. 为 OpenAI Agents SDK 提供可调用工具列表。
    3. 为测试环境提供手工调用入口。
    """

    def __init__(self, *, device_state_reader: Callable[[], dict[str, Any]]) -> None:
        self._device_state_reader = device_state_reader
        self._tools: dict[str, RegisteredTool] = {}
        self._register_query_device_state_tool()

    def list_sdk_tools(self) -> list[Any]:
        """返回全部 SDK Tool。

        返回值：
        1. 可直接传给 OpenAI Agents SDK 的工具列表。
        """

        return [tool.sdk_tool for tool in self._tools.values()]

    def get_device_state_reader(self) -> Callable[[], dict[str, Any]]:
        """返回设备状态读取函数。

        返回值：
        1. 当前注册表绑定的设备状态读取函数。
        """

        return self._device_state_reader

    def invoke(self, *, name: str, context: AgentToolContext, arguments: dict[str, Any] | None = None) -> Any:
        """手工调用指定工具。

        参数：
        1. `name`：工具名称。
        2. `context`：工具调用上下文。
        3. `arguments`：工具参数字典。

        返回值：
        1. 工具执行结果。

        异常情况：
        1. 工具不存在时抛出结构化错误。
        """

        tool = self._tools.get(name)
        if tool is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "指定工具不存在",
                details={"tool_name": name},
            )
        return tool.invoke_handler(context=context, **(arguments or {}))

    def _register_query_device_state_tool(self) -> None:
        """注册设备状态查询工具。"""

        def _build_trace(*, context: AgentToolContext, target_device_id: str | None) -> CapabilityTrace:
            return CapabilityTrace(
                trace_id=f"cap_{context.turn_id}_{now_ms()}",
                turn_id=context.turn_id,
                capability_type="tool",
                capability_name="query_device_state",
                status="running",
                input_summary=json.dumps(
                    {
                        "target_device_id": target_device_id or context.device_id,
                    },
                    ensure_ascii=False,
                ),
                started_at_ms=now_ms(),
            )

        def _normalize_device_snapshot(snapshot: dict[str, Any], device_id: str) -> dict[str, Any] | None:
            if "voice_sessions" in snapshot and isinstance(snapshot["voice_sessions"], dict):
                session_snapshot = snapshot["voice_sessions"].get(device_id)
                if isinstance(session_snapshot, dict):
                    return session_snapshot
                return None
            candidate = snapshot.get(device_id)
            return candidate if isinstance(candidate, dict) else None

        def invoke_query_device_state(
            *,
            context: AgentToolContext,
            target_device_id: str | None = None,
        ) -> dict[str, Any]:
            """查询指定设备的当前状态。

            功能：
            1. 读取当前运行时里的设备状态快照。
            2. 返回目标设备的会话和语音链路状态。

            参数：
            1. `context`：工具调用上下文。
            2. `target_device_id`：待查询设备编号；为空时默认查询当前设备。

            返回值：
            1. 结构化设备状态字典。

            异常情况：
            1. 若目标设备不存在，则抛出结构化错误。
            """

            trace = _build_trace(context=context, target_device_id=target_device_id)
            device_id = (target_device_id or context.device_id).strip()
            try:
                snapshot = context.device_state_reader()
                device_snapshot = _normalize_device_snapshot(snapshot, device_id)
                if device_snapshot is None:
                    raise build_error(
                        ErrorCode.STREAM_NOT_FOUND,
                        "目标设备当前不在线或状态未知",
                        details={"device_id": device_id},
                    )
                result = {
                    "device_id": device_id,
                    "online": True,
                    "state": str(device_snapshot.get("state", "unknown")),
                    "session_id": device_snapshot.get("session_id"),
                    "audio_connection_online": bool(device_snapshot.get("audio_connection_online", False)),
                    "reply_stream_id": device_snapshot.get("reply_stream_id"),
                }
                trace.status = "succeeded"
                trace.output_summary = json.dumps(result, ensure_ascii=False)
                trace.completed_at_ms = now_ms()
                context.trace_sink(trace)
                return result
            except Exception as exc:
                trace.status = "failed"
                trace.error_message = str(exc)
                trace.completed_at_ms = now_ms()
                context.trace_sink(trace)
                raise

        @function_tool(
            name_override="query_device_state",
            failure_error_function=lambda _ctx, exc: f"工具 query_device_state 调用失败：{exc}",
        )
        def sdk_query_device_state(
            ctx: RunContextWrapper[AgentToolContext],
            target_device_id: str | None = None,
        ) -> dict[str, Any]:
            """查询设备当前运行状态。

            功能：
            1. 查询当前或指定设备的在线状态与语音会话状态。
            2. 适用于回答“设备现在怎么样”“还在监听吗”等问题。

            参数：
            1. `target_device_id`：待查询设备编号；为空时默认查询当前会话设备。

            返回值：
            1. 包含 `device_id`、`state`、`session_id`、`audio_connection_online` 的字典。

            异常情况：
            1. 设备不存在或不在线时，返回给模型明确错误信息。
            """

            return invoke_query_device_state(context=ctx.context, target_device_id=target_device_id)

        self._tools["query_device_state"] = RegisteredTool(
            name="query_device_state",
            description="查询设备运行状态",
            sdk_tool=sdk_query_device_state,
            invoke_handler=invoke_query_device_state,
        )
