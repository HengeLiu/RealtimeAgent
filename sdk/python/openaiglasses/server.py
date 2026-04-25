"""SDK 到真实服务端运行时的集成入口。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from agent_core import AgentFacade, ToolGateway, ToolRegistry
from agent_core.context import AgentSessionStore
from agent_core.models import CapabilityResult as AgentCapabilityResult
from agent_core.models import ToolSpec
from agent_core.runtime import OpenAIAgentLoopRunner
from agent_core.tools.base import AgentToolContext, BaseTool as AgentBaseTool
from backend_task_core import InMemoryTaskGateway, TaskEvent, TaskGateway, TaskRuntime
from backend_task_core.event_bus import TaskEventBus
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from openaiglasses.models import CapabilityResult as SdkCapabilityResult
from openaiglasses.runtime import TaskRuntimeSnapshot
from openaiglasses.sdk import OpenAIGlassesSDK

if TYPE_CHECKING:
    from api.http_server import ServerHandle


class GenericSdkToolInput(BaseModel):
    """允许任意键值的宽松输入模型。"""

    model_config = ConfigDict(extra="allow")


def _sdk_snapshot_to_backend_runtime(snapshot: TaskRuntimeSnapshot) -> TaskRuntime:
    """把 SDK 任务快照转换成 backend-task-core 运行态。"""

    return TaskRuntime(
        task_id=snapshot.task_id,
        task_type=snapshot.task_type,
        version="sdk-v1",
        session_id=snapshot.session_id,
        device_id=snapshot.device_id,
        state=snapshot.state,
        input=dict(snapshot.input_data),
        context=dict(snapshot.data),
        result=dict(snapshot.result) if snapshot.result is not None else None,
        error=dict(snapshot.error) if snapshot.error is not None else None,
    )


@dataclass(slots=True)
class SdkToolAdapter(AgentBaseTool):
    """把 SDK Tool 暴露给 agent-core。"""

    sdk_tool: Any

    def __post_init__(self) -> None:
        """生成 agent-core 可识别的 ToolSpec。"""

        input_model = getattr(self.sdk_tool, "input_model", None) or GenericSdkToolInput
        output_model = getattr(self.sdk_tool, "output_model", None)
        self.spec = ToolSpec(
            name=str(getattr(self.sdk_tool, "name", "")).strip(),
            description=str(getattr(self.sdk_tool, "description", "")).strip(),
            input_model=input_model,
            output_model=output_model,
            capability_type="tool",
            tags=["sdk"],
        )

    def run(self, context: AgentToolContext, input_data) -> AgentCapabilityResult:
        """执行 SDK Tool。"""

        factory = context.device_group_context_factory
        if factory is None:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "当前运行时未绑定 DeviceGroupContext 工厂，无法执行 SDK Tool",
                details={"tool_name": self.spec.name},
            )

        device_group_context = factory(
            device_id=context.device_id,
            session_id=context.session_id,
        )
        sdk_result = self.sdk_tool.run(
            device_group_context,
            input_data.model_dump(exclude_none=True),
        )
        return _sdk_result_to_agent_result(sdk_result)


class HybridTaskGateway(TaskGateway):
    """同时支持 backend-task-core 与 SDK 自定义任务的混合网关。"""

    def __init__(
        self,
        *,
        base_gateway: TaskGateway,
        sdk_task_runtime,
    ) -> None:
        self._base_gateway = base_gateway
        self._sdk_task_runtime = sdk_task_runtime
        self._sdk_event_bus = TaskEventBus()

    def bind_device_groups(self, device_groups: Any) -> None:
        """把 SDK 任务运行时绑定到真实设备组运行时。"""

        self._sdk_task_runtime.bind_device_groups(device_groups)

    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        """创建任务。"""

        try:
            return self._base_gateway.create_task(
                task_type=task_type,
                session_id=session_id,
                device_id=device_id,
                input_data=input_data,
            )
        except AppError as exc:
            if exc.code != ErrorCode.TASK_NOT_FOUND or not self._sdk_task_runtime.has_task(task_type):
                raise

        snapshot = self._sdk_task_runtime.create_task(
            task_type=task_type,
            session_id=session_id,
            device_id=device_id,
            input_data=input_data,
        )
        runtime = _sdk_snapshot_to_backend_runtime(snapshot)
        self._publish_sdk_event(runtime=runtime, event_name="task.created", payload={"message": "SDK 任务已创建"})
        if runtime.state == "running":
            self._publish_sdk_event(runtime=runtime, event_name="task.started", payload={"message": "SDK 任务已启动"})
        elif runtime.state == "completed":
            self._publish_sdk_event(
                runtime=runtime,
                event_name="task.completed",
                payload=dict(runtime.result or {}),
                allow_direct_notify=True,
            )
        return runtime

    def query_task(self, task_id: str) -> TaskRuntime:
        """查询任务。"""

        try:
            return self._base_gateway.query_task(task_id)
        except AppError as exc:
            if exc.code != ErrorCode.TASK_NOT_FOUND or not self._sdk_task_runtime.contains_task(task_id):
                raise
        return _sdk_snapshot_to_backend_runtime(self._sdk_task_runtime.query_task(task_id))

    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消任务。"""

        try:
            return self._base_gateway.cancel_task(task_id)
        except AppError as exc:
            if exc.code != ErrorCode.TASK_NOT_FOUND or not self._sdk_task_runtime.contains_task(task_id):
                raise
        snapshot = self._sdk_task_runtime.cancel_task(task_id)
        runtime = _sdk_snapshot_to_backend_runtime(snapshot)
        self._publish_sdk_event(runtime=runtime, event_name="task.cancelled", payload={"message": "SDK 任务已取消"})
        return runtime

    def report_find_object_result(
        self,
        *,
        task_id: str,
        found: bool,
        target_object: str,
        confidence: float,
        position: str,
        frame_seq: int | None,
        summary: str,
    ) -> TaskRuntime:
        """上报找物体检测结果。"""

        try:
            return self._base_gateway.report_find_object_result(
                task_id=task_id,
                found=found,
                target_object=target_object,
                confidence=confidence,
                position=position,
                frame_seq=frame_seq,
                summary=summary,
            )
        except AppError as exc:
            if exc.code != ErrorCode.TASK_NOT_FOUND or not self._sdk_task_runtime.contains_task(task_id):
                raise

        snapshot = self._sdk_task_runtime.dispatch_event(
            task_id=task_id,
            event_name="phone.vision.find_object.result",
            payload={
                "found": found,
                "target_object": target_object,
                "confidence": confidence,
                "position": position,
                "frame_seq": frame_seq,
                "summary": summary,
            },
            source="phone",
        )
        runtime = _sdk_snapshot_to_backend_runtime(snapshot)
        if runtime.state == "completed":
            self._publish_sdk_event(
                runtime=runtime,
                event_name="task.completed",
                payload=dict(runtime.result or {}),
                allow_direct_notify=True,
            )
        return runtime

    def subscribe_events(self, listener) -> None:
        """订阅任务事件。"""

        self._base_gateway.subscribe_events(listener)
        self._sdk_event_bus.subscribe(listener)

    def shutdown(self) -> None:
        """关闭混合网关。"""

        self._base_gateway.shutdown()

    def _publish_sdk_event(
        self,
        *,
        runtime: TaskRuntime,
        event_name: str,
        payload: dict[str, Any],
        allow_direct_notify: bool = False,
    ) -> None:
        """发布 SDK 任务事件。"""

        self._sdk_event_bus.publish(
            TaskEvent(
                event_id=f"sdk_evt_{runtime.task_id}_{event_name}",
                event_name=event_name,
                task_id=runtime.task_id,
                task_type=runtime.task_type,
                session_id=runtime.session_id,
                device_id=runtime.device_id,
                state=runtime.state,
                priority="normal",
                requires_agent_decision=False,
                allow_direct_notify=allow_direct_notify,
                ts=0,
                payload=payload,
            )
        )


def _sdk_result_to_agent_result(result: SdkCapabilityResult) -> AgentCapabilityResult:
    """把 SDK Tool 结果转换为 agent-core 结果。"""

    if result.ok:
        return AgentCapabilityResult.success(
            data=dict(result.data),
            message=result.message,
            meta=dict(result.meta),
        )
    return AgentCapabilityResult.failed(
        code=str(result.error.code if result.error is not None else "sdk_tool_failed"),
        message=result.message or (result.error.message if result.error is not None else "SDK Tool 调用失败"),
        details=dict(result.error.details if result.error is not None else {}),
        meta=dict(result.meta),
    )


def build_agent_facade_from_sdk(
    *,
    sdk: OpenAIGlassesSDK,
    settings: ServerSettings,
) -> AgentFacade:
    """基于 SDK 构建真实服务端可用的 AgentFacade。"""

    session_store = AgentSessionStore()
    hybrid_task_gateway = HybridTaskGateway(
        base_gateway=InMemoryTaskGateway(),
        sdk_task_runtime=sdk.task_runtime,
    )
    tool_registry = ToolRegistry(
        device_state_reader=lambda: {},
        task_gateway=hybrid_task_gateway,
    )
    tool_gateway = ToolGateway(tool_registry)
    tool_registry.bind_gateway(tool_gateway)

    for tool_name in sdk.registry.list_tool_names():
        sdk_tool = sdk.registry.get_tool(tool_name)
        if sdk_tool is None:
            continue
        tool_registry.register_external_tool(
            SdkToolAdapter(sdk_tool=sdk_tool),
            expose_to_model=bool(getattr(sdk_tool, "expose_to_model", True)),
        )

    runner = OpenAIAgentLoopRunner(
        settings=settings,
        session_store=session_store,
        tool_registry=tool_registry,
        tool_gateway=tool_gateway,
    )
    return AgentFacade(
        session_store=session_store,
        tool_registry=tool_registry,
        runner=runner,
        system_prompt=settings.voice_system_prompt,
    )


def build_server_handle_from_sdk(
    *,
    sdk: OpenAIGlassesSDK,
    settings: ServerSettings,
) -> "ServerHandle":
    """基于 SDK 构建真实服务端句柄。"""

    from api.http_server import build_server_handle

    facade = build_agent_facade_from_sdk(sdk=sdk, settings=settings)
    handle = build_server_handle(settings, agent_facade=facade)
    sdk.device_groups = handle.runtime.device_group_runtime
    return handle
