"""第二期 SDK 骨架测试。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from urllib.request import urlopen

import pytest
from pydantic import BaseModel

from host.server.main import create_sdk, create_server_handle
from agent_core.mcp import BaseMcpAdapter
from agent_core.models import CapabilityResult as AgentCapabilityResult
from agent_core.models import McpMethodSpec
from backend_task_core import InMemoryTaskGateway
from infra.config import ServerSettings
from openaiglasses import (
    BackendTaskGatewayAdapter,
    BaseTask,
    BasePhoneProcessor,
    BasePhoneTask,
    BaseSensorProvider,
    HybridTaskGateway,
    OpenAIGlassesSDK,
    PhoneTaskContext,
    SensorReading,
    build_agent_facade_from_sdk,
)
from agent_core.tools.base import AgentToolContext


ROOT = Path(__file__).resolve().parents[3]
BLIND_APP_ROOT = ROOT / "openaiglass-for-blind"


def test_sdk_registry_can_load_blind_business_capability() -> None:
    """测试目标：验证盲人业务能力可注册到 SDK。

    测试方法：
    1. 调用 `host.server.main.create_sdk()` 创建 SDK。
    2. 检查 Tool、Task、PhoneProcessor 和 PhoneTask 注册表。

    预期结果：
    1. 注册表中包含 `start_find_object`、`find_object_task` 和 `yolo_find_object`。
    """

    sdk = create_sdk()

    assert sdk.registry.list_tool_names() == ["start_find_object"]
    assert sdk.registry.list_task_types() == ["find_object_task"]
    assert sdk.registry.list_phone_processor_types() == ["yolo_find_object"]
    assert sdk.registry.list_phone_task_types() == ["find_object_phone_task"]


def test_sdk_registry_rejects_duplicate_tool_registration() -> None:
    """测试目标：验证能力注册表会拒绝重复 Tool 名称。

    测试方法：
    1. 创建一个空 SDK。
    2. 连续注册两个同名 Tool。

    预期结果：
    1. 第二次注册抛出 `ValueError`。
    """

    class DemoToolA:
        name = "demo_tool"

    class DemoToolB:
        name = "demo_tool"

    sdk = OpenAIGlassesSDK()
    sdk.registry.register_tool(DemoToolA())

    try:
        sdk.registry.register_tool(DemoToolB())
    except ValueError as exc:
        assert "tool 已存在重复注册" in str(exc)
    else:  # pragma: no cover - 防止误通过
        raise AssertionError("重复 Tool 注册应抛出 ValueError")


def test_device_group_runtime_hides_binding_details() -> None:
    """测试目标：验证 DeviceGroupRuntime 可以创建设备组上下文。

    测试方法：
    1. 注册模拟眼镜和手机。
    2. 绑定两个设备。
    3. 通过 `DeviceGroupContext` 查询设备。

    预期结果：
    1. 上下文可以读取眼镜和手机端点。
    2. 开发者不需要直接读取底层绑定表。
    """

    sdk = OpenAIGlassesSDK()
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass")
    runtime.register_device(device_id="phone_001", role="phone")
    runtime.bind_devices(glass_device_id="glass_001", phone_device_id="phone_001")

    context = runtime.create_context(device_id="glass_001", session_id="sess_001")

    assert context.require_glass().device_id == "glass_001"
    assert context.require_phone().device_id == "phone_001"
    assert {item.role for item in context.query_devices()} == {"glass", "phone"}


def test_device_group_context_can_call_registered_mcp_adapter() -> None:
    """测试目标：验证业务上下文可通过 SDK 统一入口调用 MCP。

    测试方法：
    1. 创建一个 mock MCP adapter 并注册到 SDK。
    2. 通过 `DeviceGroupContext.mcp(...)` 调用 adapter 方法。
    3. 检查返回结果和 MCP 调用轨迹。

    预期结果：
    1. 业务侧不需要直接构造 `McpRegistry` 或 `McpGateway`。
    2. MCP 调用返回结构化成功结果。
    3. 设备组运行时记录 `capability_type=mcp` 的调用轨迹。
    """

    class RouteInput(BaseModel):
        """路线规划入参。"""

        origin: str
        destination: str
        strategy: str = "walking"

    class MockMapAdapter(BaseMcpAdapter):
        """用于测试的地图 MCP adapter。"""

        adapter_name = "mock_map"

        def list_methods(self) -> list[McpMethodSpec]:
            """返回测试方法清单。"""

            return [
                McpMethodSpec(
                    name="map.route_plan",
                    description="测试路线规划",
                    input_model=RouteInput,
                )
            ]

        def invoke(self, *, method_name: str, context: AgentToolContext, input_data) -> AgentCapabilityResult:
            """返回固定路线规划结果。"""

            return AgentCapabilityResult.success(
                data={
                    "method_name": method_name,
                    "summary": f"{input_data.origin}->{input_data.destination}",
                    "strategy": input_data.strategy,
                    "session_id": context.session_id,
                }
            )

    sdk = OpenAIGlassesSDK()
    sdk.register_mcp_adapter(MockMapAdapter())
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass")
    context = runtime.create_context(device_id="glass_001", session_id="sess_mcp_001")

    result = context.mcp(
        "map.route_plan",
        {
            "origin": "家",
            "destination": "地铁站",
            "strategy": "walking",
        },
    )

    assert result.ok is True
    assert result.data["summary"] == "家->地铁站"
    assert result.data["session_id"] == "sess_mcp_001"
    traces = runtime.list_mcp_traces()
    assert len(traces) == 1
    assert traces[0].capability_type == "mcp"
    assert traces[0].capability_name == "map.route_plan"


def test_blind_business_tool_can_create_managed_task() -> None:
    """测试目标：验证盲人业务 Tool 会通过 SDK 真正创建任务。

    测试方法：
    1. 创建示例 SDK 并注册模拟设备组。
    2. 通过 `start_find_object` 工具创建任务。
    3. 查询 SDK 托管任务快照。

    预期结果：
    1. 工具返回成功结果和任务编号。
    2. 任务已经进入 `running` 状态。
    """

    sdk = create_sdk()
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass")
    runtime.register_device(device_id="phone_001", role="phone")
    runtime.bind_devices(glass_device_id="glass_001", phone_device_id="phone_001")
    runtime.video_link_start_adapter = lambda **kwargs: {"ok": True, **kwargs}
    runtime.device_command_adapter = lambda **kwargs: {"ok": True, **kwargs}
    context = runtime.create_context(device_id="glass_001", session_id="sess_001")

    tool = sdk.registry.get_tool("start_find_object")
    assert tool is not None
    result = tool.run(context, {"target_object": "水杯"})

    assert result.ok is True
    task_id = str(result.data["task_id"])
    task_snapshot = sdk.task_runtime.query_task(task_id)
    assert task_snapshot.state == "running"
    assert task_snapshot.input_data["target_object"] == "水杯"


def test_backend_task_gateway_adapter_rejects_system_task_after_sdk_boundary_move() -> None:
    """测试目标：验证旧后台任务网关桥接器不再直接承载系统视频直连任务。

    测试方法：
    1. 创建一个真实 `InMemoryTaskGateway`。
    2. 用 `BackendTaskGatewayAdapter` 包装。
    3. 尝试创建 `phone_video_link_task`。

    预期结果：
    1. 抛出任务不存在错误。
    2. 错误语义说明该系统任务已经转交 SDK 集成层托管。
    """

    gateway = InMemoryTaskGateway()
    try:
        adapter = BackendTaskGatewayAdapter(task_gateway=gateway)
        with pytest.raises(Exception, match="未找到对应任务模板"):
            adapter.create_task(
                task_type="phone_video_link_task",
                session_id="sess_phone_video_001",
                device_id="glass-001",
                input_data={
                    "phone_device_id": "phone-001",
                    "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
                    "frame_interval_ms": 500,
                },
            )
    finally:
        gateway.shutdown()


def test_phone_runtime_can_run_phone_task_with_processor() -> None:
    """测试目标：验证手机运行时可以调度 PhoneTask 和 PhoneProcessor。

    测试方法：
    1. 创建包含官方示例手机能力的 SDK。
    2. 启动 `find_object_phone_task`。
    3. 向任务输入一帧包含目标物体名称的文本帧。

    预期结果：
    1. 手机任务进入 `running`。
    2. 任务结果中出现处理器输出的结构化检测结果。
    """

    sdk = create_sdk()
    snapshot = sdk.phone_runtime.start_task(
        task_type="find_object_phone_task",
        params={
            "target_object": "水杯",
            "processor_type": "yolo_find_object",
        },
    )

    assert snapshot.state == "running"
    updated = sdk.phone_runtime.process_task_frame(
        task_id=snapshot.task_id,
        frame="画面里有一个蓝色水杯",
    )

    assert updated.results[-1]["found"] is True
    assert updated.results[-1]["target_object"] == "水杯"


def test_phone_runtime_can_list_task_snapshots() -> None:
    """测试目标：验证手机运行时可以列出当前全部任务快照。

    测试方法：
    1. 创建包含官方示例手机能力的 SDK。
    2. 连续启动两个 `find_object_phone_task`。
    3. 调用 `phone_runtime.list_tasks()`。

    预期结果：
    1. 返回两个任务快照。
    2. 每个快照都保留独立的任务编号和运行状态。
    """

    sdk = create_sdk()
    first = sdk.phone_runtime.start_task(
        task_type="find_object_phone_task",
        params={
            "target_object": "水杯",
            "processor_type": "yolo_find_object",
        },
    )
    second = sdk.phone_runtime.start_task(
        task_type="find_object_phone_task",
        params={
            "target_object": "钥匙",
            "processor_type": "yolo_find_object",
        },
    )

    tasks = sdk.phone_runtime.list_tasks()

    assert len(tasks) == 2
    assert {item.task_id for item in tasks} == {first.task_id, second.task_id}
    assert {item.state for item in tasks} == {"running"}


def test_phone_task_context_can_query_self_snapshot() -> None:
    """测试目标：验证手机任务上下文可以查询自身快照。

    测试方法：
    1. 注册一个在 `on_start` 中调用 `query_self()` 的手机任务。
    2. 启动该任务并记录读取到的快照。

    预期结果：
    1. 上下文可以读取当前任务编号。
    2. 读取到的快照包含最新状态数据。
    """

    class InspectSelfPhoneTask(BasePhoneTask):
        task_type = "inspect_self_phone_task"

        def on_start(self, context: PhoneTaskContext) -> None:
            context.emit_state("running", {"phase": "started"})
            snapshot = context.query_self()
            context.update({"snapshot_task_id": snapshot.task_id, "snapshot_state": snapshot.state})

    sdk = OpenAIGlassesSDK()
    sdk.register_phone_task(InspectSelfPhoneTask())

    snapshot = sdk.phone_runtime.start_task(
        task_type="inspect_self_phone_task",
        params={},
    )

    assert snapshot.state == "running"
    assert snapshot.data["snapshot_task_id"] == snapshot.task_id
    assert snapshot.data["snapshot_state"] == "running"


def test_phone_runtime_can_read_registered_sensor_provider() -> None:
    """测试目标：验证手机运行时可以读取注册的传感器提供者。

    测试方法：
    1. 注册一个简单的方向角传感器提供者。
    2. 通过 `phone_runtime.read_sensor(...)` 读取数据。

    预期结果：
    1. 返回 `SensorReading`。
    2. 读数中包含期望的方向角数据。
    """

    class HeadingSensorProvider(BaseSensorProvider):
        sensor_type = "heading"

        def read(self) -> SensorReading:
            return SensorReading(
                sensor_type="heading",
                payload={"heading_degrees": 87},
                timestamp_ms=123,
            )

    sdk = OpenAIGlassesSDK()
    sdk.register_sensor_provider(HeadingSensorProvider())

    reading = sdk.phone_runtime.read_sensor("heading")

    assert reading.sensor_type == "heading"
    assert reading.payload["heading_degrees"] == 87


def test_hybrid_task_gateway_can_run_sdk_only_task() -> None:
    """测试目标：验证混合任务网关可以承接 backend-task-core 未注册的 SDK 任务。

    测试方法：
    1. 注册一个只存在于 SDK 的即时完成任务。
    2. 用 `HybridTaskGateway` 包装原有 `InMemoryTaskGateway`。
    3. 创建并查询该任务。

    预期结果：
    1. 任务由 SDK 侧运行时创建。
    2. 查询结果为 `completed`，并包含结构化结果。
    """

    class ImmediateTask(BaseTask):
        task_type = "immediate_task"

        def on_start(self, context) -> None:
            context.emit_state("running", {"phase": "started"})
            context.complete({"done": True})

    sdk = OpenAIGlassesSDK()
    sdk.register_task(ImmediateTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")
    base_gateway = InMemoryTaskGateway()
    try:
        gateway = HybridTaskGateway(
            base_gateway=base_gateway,
            sdk_task_runtime=sdk.task_runtime,
        )
        runtime = gateway.create_task(
            task_type="immediate_task",
            session_id="sess_sdk_task_001",
            device_id="glass_001",
            input_data={},
        )

        assert runtime.state == "completed"
        assert runtime.result == {"done": True}
        latest = gateway.query_task(runtime.task_id)
        assert latest.task_type == "immediate_task"
        assert latest.result == {"done": True}
    finally:
        gateway.shutdown()


def test_hybrid_task_gateway_can_forward_generic_event_to_sdk_task() -> None:
    """测试目标：验证混合任务网关可把通用事件推进到 SDK 任务。

    测试方法：
    1. 注册一个等待手机事件的 SDK 任务。
    2. 通过 `HybridTaskGateway` 创建该任务。
    3. 调用 `dispatch_event(...)` 上报一次通用事件。

    预期结果：
    1. 任务从 `running` 进入 `completed`。
    2. 结果中包含手机侧上报的业务字段。
    """

    class WaitingFindObjectTask(BaseTask):
        task_type = "waiting_find_object_task"

        def on_start(self, context) -> None:
            context.emit_state("running")

        def on_event(self, context, event) -> None:
            if event.name != "phone.vision.find_object.result":
                return
            if event.payload.get("found"):
                context.complete({"target_object": event.payload.get("target_object"), "found": True})

    sdk = OpenAIGlassesSDK()
    sdk.register_task(WaitingFindObjectTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")
    base_gateway = InMemoryTaskGateway()
    try:
        gateway = HybridTaskGateway(
            base_gateway=base_gateway,
            sdk_task_runtime=sdk.task_runtime,
        )
        runtime = gateway.create_task(
            task_type="waiting_find_object_task",
            session_id="sess_sdk_find_001",
            device_id="glass_001",
            input_data={},
        )

        assert runtime.state == "running"
        updated = gateway.dispatch_event(
            task_id=runtime.task_id,
            event_name="phone.vision.find_object.result",
            payload={
                "found": True,
                "target_object": "水杯",
                "confidence": 0.91,
                "position": "center",
                "frame_seq": 3,
                "summary": "找到水杯了",
            },
            source="phone",
        )

        assert updated.state == "completed"
        assert updated.result == {"target_object": "水杯", "found": True}
    finally:
        gateway.shutdown()


def test_sdk_task_runtime_records_events_and_times_out() -> None:
    """测试目标：验证 SDK 托管任务具备事件日志和查询触发超时能力。

    测试方法：
    1. 注册一个进入 `running` 的等待任务。
    2. 创建任务时传入 `timeout_ms`。
    3. 人为把恢复截止时间调到过去并查询任务。

    预期结果：
    1. 任务进入 `timeout`。
    2. 快照中包含 `task.created`、`task.started`、`task.timeout` 事件。
    3. 快照保留结构化错误和时间戳。
    """

    class WaitingTask(BaseTask):
        task_type = "sdk_waiting_task"

        def on_start(self, context) -> None:
            context.emit_state("running", {"phase": "waiting"})

    sdk = OpenAIGlassesSDK()
    sdk.register_task(WaitingTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")

    created = sdk.task_runtime.create_task(
        task_type="sdk_waiting_task",
        session_id="sess_timeout_001",
        device_id="glass_001",
        input_data={"timeout_ms": 1000},
    )
    sdk.task_runtime._records[created.task_id].deadline_at_ms = 1  # noqa: SLF001

    timeout = sdk.task_runtime.query_task(created.task_id)

    assert timeout.state == "timeout"
    assert timeout.error is not None
    assert timeout.error["code"] == "task_timeout"
    assert timeout.created_at_ms > 0
    assert timeout.completed_at_ms is not None
    assert [event["event_name"] for event in timeout.events] == [
        "task.created",
        "task.started",
        "task.timeout",
    ]


def test_sdk_task_runtime_can_export_and_restore_snapshots() -> None:
    """测试目标：验证 SDK 托管任务可通过快照导出和恢复。

    测试方法：
    1. 创建一个等待外部事件的 SDK 任务并导出快照。
    2. 构造新的 SDK 运行时并恢复该快照。
    3. 继续派发外部事件完成任务。

    预期结果：
    1. 恢复后可查询原任务状态和事件日志。
    2. 已注册任务类型仍可继续接收事件并完成。
    3. 事件日志包含 `task.restored` 与最终 `task.completed`。
    """

    class RestorableTask(BaseTask):
        task_type = "sdk_restorable_task"

        def on_start(self, context) -> None:
            context.emit_state("running", {"phase": "waiting"})

        def on_event(self, context, event) -> None:
            if event.name == "phone.demo.done":
                context.complete({"ok": True, "source": event.source})

    sdk = OpenAIGlassesSDK()
    sdk.register_task(RestorableTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")
    created = sdk.task_runtime.create_task(
        task_type="sdk_restorable_task",
        session_id="sess_restore_001",
        device_id="glass_001",
        input_data={"target": "demo"},
    )
    exported = sdk.task_runtime.export_snapshots()

    restored_sdk = OpenAIGlassesSDK()
    restored_sdk.register_task(RestorableTask())
    restored_sdk.device_groups.register_device(device_id="glass_001", role="glass")
    restored = restored_sdk.task_runtime.restore_snapshots(exported)

    assert restored[0].task_id == created.task_id
    assert restored[0].state == "running"
    updated = restored_sdk.task_runtime.dispatch_event(
        task_id=created.task_id,
        event_name="phone.demo.done",
        payload={},
        source="phone",
    )

    assert updated.state == "completed"
    assert updated.result == {"ok": True, "source": "phone"}
    event_names = [event["event_name"] for event in updated.events]
    assert "task.restored" in event_names
    assert "task.completed" in event_names


def test_sdk_task_runtime_can_save_and_load_snapshot_file(tmp_path) -> None:
    """测试目标：验证 SDK 托管任务可保存到 JSON 文件并从文件恢复。

    测试方法：
    1. 创建一个运行中的 SDK 任务。
    2. 调用 `save_snapshots(...)` 写入临时 JSON 文件。
    3. 用新的 SDK 运行时调用 `load_snapshots(...)`。

    预期结果：
    1. 快照文件包含任务列表。
    2. 新运行时可查询恢复后的原任务。
    3. 恢复事件被追加到任务事件日志中。
    """

    class PersistedTask(BaseTask):
        task_type = "sdk_persisted_task"

        def on_start(self, context) -> None:
            context.emit_state("running", {"phase": "persisted"})

    sdk = OpenAIGlassesSDK()
    sdk.register_task(PersistedTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")
    created = sdk.task_runtime.create_task(
        task_type="sdk_persisted_task",
        session_id="sess_persist_001",
        device_id="glass_001",
        input_data={"target": "demo"},
    )
    snapshot_file = tmp_path / "tasks.json"

    written = sdk.task_runtime.save_snapshots(snapshot_file)

    assert snapshot_file.exists()
    assert written[0]["task_id"] == created.task_id

    restored_sdk = OpenAIGlassesSDK()
    restored_sdk.register_task(PersistedTask())
    restored_sdk.device_groups.register_device(device_id="glass_001", role="glass")
    restored = restored_sdk.task_runtime.load_snapshots(snapshot_file)

    assert restored[0].task_id == created.task_id
    latest = restored_sdk.task_runtime.query_task(created.task_id)
    assert latest.data["phase"] == "persisted"
    assert latest.events[-1]["event_name"] == "task.restored"


def test_phone_runtime_can_fanout_frame_to_matching_active_tasks() -> None:
    """测试目标：验证手机运行时可把同一帧分发给多个匹配的活跃任务。

    测试方法：
    1. 注册两个通用手机任务。
    2. 启动两个绑定同一 `stream_id` 的任务和一个绑定其它流的任务。
    3. 调用 `process_frame(...)` 分发一帧。

    预期结果：
    1. 同一路视频流上的两个任务都收到帧。
    2. 其它视频流上的任务不会收到帧。
    3. 每个命中任务记录 `frames_processed`。
    """

    class CountingPhoneTask(BasePhoneTask):
        task_type = "counting_phone_task"

        def on_start(self, context) -> None:
            context.emit_state("running")

        def on_frame(self, context, frame) -> None:
            count = int(context.data.get("count") or 0) + 1
            context.update({"count": count, "last_frame": frame})

    sdk = OpenAIGlassesSDK()
    sdk.register_phone_task(CountingPhoneTask())
    first = sdk.phone_runtime.start_task(
        task_type="counting_phone_task",
        params={"stream_id": "stream_shared", "name": "first"},
    )
    second = sdk.phone_runtime.start_task(
        task_type="counting_phone_task",
        params={"stream_id": "stream_shared", "name": "second"},
    )
    other = sdk.phone_runtime.start_task(
        task_type="counting_phone_task",
        params={"stream_id": "stream_other", "name": "other"},
    )

    updated = sdk.phone_runtime.process_frame(frame={"seq": 1}, stream_id="stream_shared")

    assert {item.task_id for item in updated} == {first.task_id, second.task_id}
    assert all(item.frames_processed == 1 for item in updated)
    assert sdk.phone_runtime.query_task(other.task_id).frames_processed == 0


def test_phone_runtime_can_filter_fanout_by_task_type_and_skip_stopped_task() -> None:
    """测试目标：验证手机帧分发支持任务类型过滤并跳过终态任务。

    测试方法：
    1. 注册两类手机任务。
    2. 停止其中一个目标任务。
    3. 用 `task_types` 限制分发范围。

    预期结果：
    1. 只有匹配任务类型且仍在运行的任务收到帧。
    2. 停止任务和其它任务类型不会收到帧。
    """

    class FirstPhoneTask(BasePhoneTask):
        task_type = "first_phone_task"

        def on_start(self, context) -> None:
            context.emit_state("running")

        def on_frame(self, context, frame) -> None:
            context.update({"frame": frame})

    class SecondPhoneTask(BasePhoneTask):
        task_type = "second_phone_task"

        def on_start(self, context) -> None:
            context.emit_state("running")

        def on_frame(self, context, frame) -> None:
            context.update({"frame": frame})

    sdk = OpenAIGlassesSDK()
    sdk.register_phone_task(FirstPhoneTask())
    sdk.register_phone_task(SecondPhoneTask())
    active = sdk.phone_runtime.start_task(task_type="first_phone_task", params={})
    stopped = sdk.phone_runtime.start_task(task_type="first_phone_task", params={})
    other_type = sdk.phone_runtime.start_task(task_type="second_phone_task", params={})
    sdk.phone_runtime.stop_task(stopped.task_id)

    updated = sdk.phone_runtime.process_frame(frame={"seq": 2}, task_types=["first_phone_task"])

    assert [item.task_id for item in updated] == [active.task_id]
    assert sdk.phone_runtime.query_task(active.task_id).frames_processed == 1
    assert sdk.phone_runtime.query_task(stopped.task_id).frames_processed == 0
    assert sdk.phone_runtime.query_task(other_type.task_id).frames_processed == 0


def test_build_agent_facade_from_sdk_registers_sdk_tools() -> None:
    """测试目标：验证 SDK 能把自定义 Tool 注入真实 agent-core 工具面。

    测试方法：
    1. 构建示例 SDK 和基于 SDK 的 `AgentFacade`。
    2. 绑定设备组上下文工厂并直接调用 `start_find_object`。

    预期结果：
    1. agent-core 中能找到 SDK Tool。
    2. Tool 调用成功并返回任务编号。
    """

    sdk = create_sdk()
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass")
    runtime.register_device(device_id="phone_001", role="phone")
    runtime.bind_devices(glass_device_id="glass_001", phone_device_id="phone_001")
    runtime.video_link_start_adapter = lambda **kwargs: {"ok": True, **kwargs}
    runtime.device_command_adapter = lambda **kwargs: {"ok": True, **kwargs}

    facade = build_agent_facade_from_sdk(
        sdk=sdk,
        settings=ServerSettings(),
    )
    registry = facade.get_tool_registry()
    registry.bind_device_group_context_factory(runtime.create_context)
    tool = registry.get("start_find_object")
    assert tool is not None

    result = registry.invoke(
        name="start_find_object",
        context=AgentToolContext(
            session_id="sess_sdk_tool_001",
            device_id="glass_001",
            turn_id="turn_sdk_tool_001",
            settings=ServerSettings(),
            session_store=None,
            device_state_reader=registry.get_device_state_reader(),
            trace_sink=lambda _trace: None,
            device_group_context_factory=registry.get_device_group_context_factory(),
            task_gateway=registry.get_task_gateway(),
            camera_gateway=registry.get_camera_gateway(),
            tool_gateway=registry._gateway,
            mcp_gateway=registry.get_mcp_gateway(),
        ),
        arguments={"target_object": "水杯"},
    )

    assert result.ok is True
    assert result.data["target_object"] == "水杯"
    assert result.data["task_id"].startswith("task_")


def test_blind_server_handle_can_build_real_runtime() -> None:
    """测试目标：验证盲人业务入口已可构建真实服务端句柄。

    测试方法：
    1. 调用 `host.server.main.create_server_handle(...)`。
    2. 检查返回句柄中的运行时和任务网关类型。

    预期结果：
    1. 句柄包含真实 `ControlRuntime`。
    2. 内部任务网关为 `HybridTaskGateway`。
    """

    handle = create_server_handle(ServerSettings(host="127.0.0.1", port=0))
    try:
        gateway = handle.runtime.voice_runtime.agent_facade.get_task_gateway()
        assert gateway.__class__.__name__ == "HybridTaskGateway"
        assert handle.runtime.device_group_runtime.task_runtime is not None
    finally:
        handle.server.server_close()


def test_sdk_build_server_handle_rebinds_live_device_group_runtime() -> None:
    """测试目标：验证 SDK 构建真实服务端后会绑定到活的设备组运行时。

    测试方法：
    1. 创建示例 SDK。
    2. 调用 `sdk.build_server_handle(...)`。
    3. 检查 SDK 当前持有的 `device_groups`。

    预期结果：
    1. SDK 上的 `device_groups` 与服务端运行时中的对象相同。
    """

    sdk = create_sdk()
    handle = sdk.build_server_handle(ServerSettings(host="127.0.0.1", port=0))
    try:
        assert sdk.device_groups is handle.runtime.device_group_runtime
    finally:
        handle.server.server_close()


def test_blind_server_handle_can_start_real_http_service() -> None:
    """测试目标：验证盲人业务服务端句柄可真实启动 HTTP 服务。

    测试方法：
    1. 构建并启动盲人业务服务端句柄。
    2. 请求 `/api/health`。

    预期结果：
    1. 返回 `status=ok`。
    2. 返回 `service=server-api`。
    """

    handle = create_server_handle(ServerSettings(host="127.0.0.1", port=0))
    try:
        handle.start()
        url = f"http://127.0.0.1:{handle.port}/api/health"
        with urlopen(url, timeout=3) as response:
            body = json.loads(response.read().decode("utf-8"))
        assert body["status"] == "ok"
        assert body["service"] == "server-api"
    finally:
        handle.stop()


def test_sdk_package_does_not_import_blind_business_code() -> None:
    """测试目标：验证 SDK 不反向依赖盲人业务代码。

    测试方法：
    1. 扫描 `openaiglass-sdk/server-python/openaiglasses` 下所有 Python 文件的 import 语句。
    2. 检查是否导入 `capabilities` 或盲人业务 `server` 入口。

    预期结果：
    1. SDK 源码中不存在对盲人业务代码的导入。
    """

    sdk_root = ROOT / "openaiglass-sdk/server-python/openaiglasses"
    offenders: list[str] = []
    for path in sdk_root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(
                name == "capabilities"
                or name.startswith("capabilities.")
                or name == "server"
                or name.startswith("server.")
                for name in names
            ):
                offenders.append(str(path.relative_to(ROOT)))

    assert offenders == []
