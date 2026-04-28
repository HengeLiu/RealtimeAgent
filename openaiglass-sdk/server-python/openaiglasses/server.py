"""SDK 到真实服务端运行时的集成入口。"""

from __future__ import annotations

import uuid
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
from backend_task_core.models import now_ms
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


def _new_system_task_id() -> str:
    """生成系统任务编号。"""

    return f"task_{uuid.uuid4().hex[:12]}"


def _new_stream_id() -> str:
    """生成视频流编号。"""

    return f"stream_{uuid.uuid4().hex[:12]}"


class SdkSystemTaskRuntime:
    """由 SDK 托管的系统级任务运行时。"""

    _TERMINAL_STATES = {"cancelled", "completed", "failed", "timeout"}
    _PEER_LINK_EVENTS = {
        "peer_link.ready",
        "peer_link.failed",
        "peer_link.broken",
        "peer_link.closed",
    }
    _CAMERA_STREAM_EVENTS = {
        "camera.stream.started",
        "camera.stream.stopped",
    }

    def __init__(self) -> None:
        self._event_bus = TaskEventBus()
        self._records: dict[str, TaskRuntime] = {}

    def subscribe_events(self, listener) -> None:
        """订阅系统任务事件。"""

        self._event_bus.subscribe(listener)

    def handles_task_type(self, task_type: str) -> bool:
        """判断是否由当前运行时托管该任务类型。"""

        return task_type == "phone_video_link_task"

    def contains_task(self, task_id: str) -> bool:
        """判断任务是否存在。"""

        return task_id in self._records

    def create_task(
        self,
        *,
        task_type: str,
        session_id: str,
        device_id: str,
        input_data: dict[str, Any],
    ) -> TaskRuntime:
        """创建系统任务。"""

        if not self.handles_task_type(task_type):
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "当前系统任务运行时不支持指定任务类型",
                details={"task_type": task_type},
            )
        phone_device_id = str(input_data.get("phone_device_id") or "").strip()
        target_ws_uri = str(input_data.get("target_ws_uri") or "").strip()
        if not phone_device_id or not target_ws_uri:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "启动视频直连任务缺少必要参数",
                details={
                    "task_type": task_type,
                    "phone_device_id": phone_device_id,
                    "target_ws_uri": target_ws_uri,
                },
            )
        frame_interval_ms = int(input_data.get("frame_interval_ms") or 500)
        if frame_interval_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "frame_interval_ms 必须大于 0",
                details={"frame_interval_ms": frame_interval_ms},
            )
        timeout_ms = int(input_data.get("timeout_ms") or 15000)
        if timeout_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "timeout_ms 必须大于 0",
                details={"timeout_ms": timeout_ms},
            )

        task_id = _new_system_task_id()
        stream_id = str(input_data.get("stream_id") or "").strip() or _new_stream_id()
        created_at_ms = now_ms()
        runtime = TaskRuntime(
            task_id=task_id,
            task_type=task_type,
            version="sdk-system-v1",
            session_id=session_id,
            device_id=device_id,
            state="running",
            input={
                "phone_device_id": phone_device_id,
                "target_ws_uri": target_ws_uri,
                "link_mode": str(input_data.get("link_mode") or "direct").strip() or "direct",
                "reason": str(input_data.get("reason") or "agent_requested").strip() or "agent_requested",
                "frame_interval_ms": frame_interval_ms,
                "timeout_ms": timeout_ms,
                "stream_id": stream_id,
            },
            context={
                "phase": "peer_link_preparing",
                "created_by": "sdk_system_task_runtime",
                "glass_device_id": device_id,
                "phone_device_id": phone_device_id,
                "target_ws_uri": target_ws_uri,
                "link_mode": str(input_data.get("link_mode") or "direct").strip() or "direct",
                "stream_id": stream_id,
                "frame_interval_ms": frame_interval_ms,
                "timeout_ms": timeout_ms,
                "deadline_at_ms": created_at_ms + timeout_ms,
                "last_peer_link_event": None,
                "last_camera_event": None,
                "last_error": None,
            },
            created_at_ms=created_at_ms,
            updated_at_ms=created_at_ms,
        )
        runtime.started_at_ms = runtime.created_at_ms
        self._records[task_id] = runtime
        self._publish_event(
            runtime=runtime,
            event_name="task.created",
            payload={
                "message": f"已创建眼镜与手机视频直连任务，目标手机是 {phone_device_id}",
                **dict(runtime.input),
            },
        )
        self._publish_event(
            runtime=runtime,
            event_name="task.started",
            payload={
                "message": f"视频直连任务已进入运行态，目标手机是 {phone_device_id}",
                **dict(runtime.input),
                "codec": "jpeg",
            },
        )
        return runtime

    def query_task(self, task_id: str) -> TaskRuntime:
        """查询系统任务。"""

        runtime = self._records.get(task_id)
        if runtime is None:
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "目标任务不存在",
                details={"task_id": task_id},
            )
        self._expire_if_needed(runtime)
        runtime.updated_at_ms = now_ms()
        return runtime

    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消系统任务。"""

        runtime = self.query_task(task_id)
        if runtime.state in self._TERMINAL_STATES:
            return runtime
        runtime.context["phase"] = "stopping"
        runtime.updated_at_ms = now_ms()
        self._publish_event(
            runtime=runtime,
            event_name="task.stopping",
            payload={
                "message": "视频直连任务正在停止",
                "phone_device_id": runtime.input.get("phone_device_id"),
                "target_ws_uri": runtime.input.get("target_ws_uri"),
                "stream_id": runtime.input.get("stream_id"),
            },
        )
        runtime.state = "cancelled"
        runtime.updated_at_ms = now_ms()
        runtime.completed_at_ms = runtime.updated_at_ms
        runtime.context["phase"] = "cancelled"
        runtime.context["cancelled_at_ms"] = runtime.updated_at_ms
        self._publish_event(
            runtime=runtime,
            event_name="task.cancelled",
            payload={
                "message": "视频直连任务已取消",
                "phone_device_id": runtime.input.get("phone_device_id"),
                "target_ws_uri": runtime.input.get("target_ws_uri"),
                "stream_id": runtime.input.get("stream_id"),
            },
        )
        return runtime

    def dispatch_event(
        self,
        *,
        task_id: str,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str = "system",
    ) -> TaskRuntime:
        """接收端侧 peer-link 或视频流事件并推进系统任务状态。

        主要逻辑：
        1. 只接受 SDK 固化的最小 peer-link 和 camera stream 事件名。
        2. 根据事件更新 `phase/state/context/result/error`。
        3. 发布同名任务事件，终态变化时额外发布 `task.completed/task.failed`。

        参数：
        1. `task_id`：系统任务编号。
        2. `event_name`：端侧上报事件名。
        3. `payload`：端侧上报的结构化数据。
        4. `source`：事件来源，通常是 `phone` 或 `glass`。

        返回值：
        1. 更新后的任务运行态。

        异常情况：
        1. 任务不存在或事件名不在标准集合内时抛出结构化错误。
        """

        runtime = self.query_task(task_id)
        normalized_event = event_name.strip()
        event_payload = dict(payload or {})
        if normalized_event not in self._PEER_LINK_EVENTS | self._CAMERA_STREAM_EVENTS:
            raise build_error(
                ErrorCode.INVALID_MESSAGE,
                "系统视频直连任务不支持该事件名",
                details={
                    "task_id": task_id,
                    "event_name": event_name,
                    "supported_events": sorted(self._PEER_LINK_EVENTS | self._CAMERA_STREAM_EVENTS),
                },
            )

        if runtime.state in self._TERMINAL_STATES:
            runtime.context["last_ignored_event"] = {
                "event_name": normalized_event,
                "source": source,
                "payload": event_payload,
                "ts": now_ms(),
            }
            runtime.updated_at_ms = now_ms()
            return runtime

        if normalized_event in self._PEER_LINK_EVENTS:
            runtime.context["last_peer_link_event"] = {
                "event_name": normalized_event,
                "source": source,
                "payload": event_payload,
                "ts": now_ms(),
            }
        if normalized_event in self._CAMERA_STREAM_EVENTS:
            runtime.context["last_camera_event"] = {
                "event_name": normalized_event,
                "source": source,
                "payload": event_payload,
                "ts": now_ms(),
            }

        if normalized_event == "peer_link.ready":
            runtime.state = "running"
            runtime.context["phase"] = "peer_link_ready"
            self._touch_runtime(runtime)
            self._publish_event(
                runtime=runtime,
                event_name=normalized_event,
                payload={"source": source, **event_payload},
            )
            return runtime

        if normalized_event == "camera.stream.started":
            runtime.state = "running"
            runtime.context["phase"] = "streaming"
            self._touch_runtime(runtime)
            self._publish_event(
                runtime=runtime,
                event_name=normalized_event,
                payload={"source": source, **event_payload},
            )
            return runtime

        if normalized_event in {"peer_link.failed", "peer_link.broken"}:
            error_code = normalized_event.replace(".", "_")
            runtime.state = "failed"
            runtime.context["phase"] = "failed"
            runtime.error = {
                "code": error_code,
                "message": str(event_payload.get("message") or event_payload.get("reason") or "视频直连链路失败"),
                "details": {
                    "event_name": normalized_event,
                    "source": source,
                    "payload": event_payload,
                },
            }
            runtime.context["last_error"] = dict(runtime.error)
            self._complete_runtime(runtime)
            self._publish_event(
                runtime=runtime,
                event_name=normalized_event,
                payload={"source": source, **event_payload},
                priority="high",
            )
            self._publish_event(
                runtime=runtime,
                event_name="task.failed",
                payload={
                    **dict(runtime.error),
                    "stream_id": runtime.input.get("stream_id"),
                    "phone_device_id": runtime.input.get("phone_device_id"),
                },
                priority="high",
            )
            return runtime

        runtime.state = "completed"
        runtime.context["phase"] = "completed"
        runtime.result = {
            "event_name": normalized_event,
            "source": source,
            "payload": event_payload,
            "message": "视频直连任务已结束",
            "stream_id": runtime.input.get("stream_id"),
            "phone_device_id": runtime.input.get("phone_device_id"),
        }
        self._complete_runtime(runtime)
        self._publish_event(
            runtime=runtime,
            event_name=normalized_event,
            payload={"source": source, **event_payload},
        )
        self._publish_event(
            runtime=runtime,
            event_name="task.completed",
            payload=dict(runtime.result),
            allow_direct_notify=True,
        )
        return runtime

    def _publish_event(
        self,
        *,
        runtime: TaskRuntime,
        event_name: str,
        payload: dict[str, Any],
        priority: str = "normal",
        allow_direct_notify: bool = False,
    ) -> None:
        """发布系统任务事件。"""

        self._event_bus.publish(
            TaskEvent(
                event_id=f"sys_evt_{runtime.task_id}_{event_name}",
                event_name=event_name,
                task_id=runtime.task_id,
                task_type=runtime.task_type,
                session_id=runtime.session_id,
                device_id=runtime.device_id,
                state=runtime.state,
                priority=priority,
                requires_agent_decision=False,
                allow_direct_notify=allow_direct_notify,
                ts=now_ms(),
                payload=payload,
            )
        )

    @staticmethod
    def _touch_runtime(runtime: TaskRuntime) -> None:
        """刷新运行态更新时间。"""

        runtime.updated_at_ms = now_ms()

    @staticmethod
    def _complete_runtime(runtime: TaskRuntime) -> None:
        """把运行态标记为已结束并写入时间戳。"""

        runtime.updated_at_ms = now_ms()
        runtime.completed_at_ms = runtime.updated_at_ms

    def _expire_if_needed(self, runtime: TaskRuntime) -> None:
        """在查询或事件派发时把超时的视频链路推进到 timeout。

        当前超时只覆盖建链和等待首帧阶段；已经进入 `streaming` 后不在本轮做
        链路健康检查，后续由 SDK 的网络治理迭代统一处理。
        """

        if runtime.state in self._TERMINAL_STATES or runtime.context.get("phase") == "streaming":
            return
        deadline_at_ms = int(runtime.context.get("deadline_at_ms") or 0)
        if deadline_at_ms <= 0 or now_ms() <= deadline_at_ms:
            return

        runtime.state = "timeout"
        runtime.context["phase"] = "timeout"
        runtime.error = {
            "code": "peer_link_timeout",
            "message": "视频直连链路准备超时",
            "details": {
                "deadline_at_ms": deadline_at_ms,
                "phone_device_id": runtime.input.get("phone_device_id"),
                "stream_id": runtime.input.get("stream_id"),
            },
        }
        runtime.context["last_error"] = dict(runtime.error)
        self._complete_runtime(runtime)
        self._publish_event(
            runtime=runtime,
            event_name="task.timeout",
            payload=dict(runtime.error),
            priority="high",
        )


class HybridTaskGateway(TaskGateway):
    """同时支持 backend-task-core 与 SDK 自定义任务的混合网关。"""

    def __init__(
        self,
        *,
        base_gateway: TaskGateway,
        sdk_task_runtime,
        system_task_runtime: SdkSystemTaskRuntime | None = None,
    ) -> None:
        self._base_gateway = base_gateway
        self._sdk_task_runtime = sdk_task_runtime
        self._system_task_runtime = system_task_runtime or SdkSystemTaskRuntime()
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

        if self._system_task_runtime.handles_task_type(task_type):
            return self._system_task_runtime.create_task(
                task_type=task_type,
                session_id=session_id,
                device_id=device_id,
                input_data=input_data,
            )

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

        if self._system_task_runtime.contains_task(task_id):
            return self._system_task_runtime.query_task(task_id)

        try:
            return self._base_gateway.query_task(task_id)
        except AppError as exc:
            if exc.code != ErrorCode.TASK_NOT_FOUND or not self._sdk_task_runtime.contains_task(task_id):
                raise
        return _sdk_snapshot_to_backend_runtime(self._sdk_task_runtime.query_task(task_id))

    def cancel_task(self, task_id: str) -> TaskRuntime:
        """取消任务。"""

        if self._system_task_runtime.contains_task(task_id):
            return self._system_task_runtime.cancel_task(task_id)

        try:
            return self._base_gateway.cancel_task(task_id)
        except AppError as exc:
            if exc.code != ErrorCode.TASK_NOT_FOUND or not self._sdk_task_runtime.contains_task(task_id):
                raise
        snapshot = self._sdk_task_runtime.cancel_task(task_id)
        runtime = _sdk_snapshot_to_backend_runtime(snapshot)
        self._publish_sdk_event(runtime=runtime, event_name="task.cancelled", payload={"message": "SDK 任务已取消"})
        return runtime

    def dispatch_event(
        self,
        *,
        task_id: str,
        event_name: str,
        payload: dict[str, Any] | None = None,
        source: str = "system",
    ) -> TaskRuntime:
        """向 SDK 托管任务派发通用事件。"""

        if self._system_task_runtime.contains_task(task_id):
            return self._system_task_runtime.dispatch_event(
                task_id=task_id,
                event_name=event_name,
                payload=dict(payload or {}),
                source=source,
            )

        if not self._sdk_task_runtime.contains_task(task_id):
            raise build_error(
                ErrorCode.TASK_NOT_FOUND,
                "目标任务不存在",
                details={"task_id": task_id},
            )
        snapshot = self._sdk_task_runtime.dispatch_event(
            task_id=task_id,
            event_name=event_name,
            payload=dict(payload or {}),
            source=source,
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
        self._system_task_runtime.subscribe_events(listener)
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
        mcp_registry=sdk.get_mcp_registry(),
        mcp_gateway=sdk.get_mcp_gateway(),
        skill_runtime=sdk.skill_runtime,
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
        skill_runtime=sdk.skill_runtime,
    )
    runner.preload_resources()
    sdk.device_groups.bind_mcp_gateway(
        sdk.get_mcp_gateway(),
        settings=settings,
        session_store=session_store,
    )
    return AgentFacade(
        session_store=session_store,
        tool_registry=tool_registry,
        runner=runner,
        system_prompt=settings.voice_system_prompt,
    )


def build_default_agent_facade(
    *,
    settings: ServerSettings,
    device_state_reader,
) -> AgentFacade:
    """构建默认服务端使用的混合门面。

    主要功能：
    1. 默认场景下也复用 SDK 系统任务托管层。
    2. 避免根 `backend_task_core` 继续内建视频直连系统任务。
    """

    session_store = AgentSessionStore()
    hybrid_task_gateway = HybridTaskGateway(
        base_gateway=InMemoryTaskGateway(),
        sdk_task_runtime=OpenAIGlassesSDK().task_runtime,
    )
    tool_registry = ToolRegistry(
        device_state_reader=device_state_reader,
        task_gateway=hybrid_task_gateway,
    )
    tool_gateway = ToolGateway(tool_registry)
    tool_registry.bind_gateway(tool_gateway)
    runner = OpenAIAgentLoopRunner(
        settings=settings,
        session_store=session_store,
        tool_registry=tool_registry,
        tool_gateway=tool_gateway,
    )
    runner.preload_resources()
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
