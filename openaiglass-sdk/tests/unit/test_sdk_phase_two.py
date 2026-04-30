"""第二期 SDK 骨架测试。"""

from __future__ import annotations

import ast
import json
import sqlite3
from pathlib import Path
from urllib.request import urlopen

import pytest
from pydantic import BaseModel

from host.server.main import create_sdk, create_server_handle
from agent_core.mcp import BaseMcpAdapter
from agent_core.models import CapabilityResult as AgentCapabilityResult
from agent_core.models import McpMethodSpec
from agent_core.runtime import OpenAIAgentLoopRunner
from backend_task_core import InMemoryTaskGateway
from infra.config import ServerSettings
from openaiglasses import (
    BackendTaskGatewayAdapter,
    BaseTask,
    BaseMcpAdapter as PublicBaseMcpAdapter,
    BasePhoneProcessor,
    BasePhoneTask,
    BaseSensorProvider,
    BaseTool,
    CapabilityResult as PublicCapabilityResult,
    McpMethodSpec as PublicMcpMethodSpec,
    HybridTaskGateway,
    MemoryConfigProvider,
    OpenAIGlassesSDK,
    PhoneTaskContext,
    SensorReading,
    SkillDocument,
    SkillManifest,
    SQLiteTaskPersistenceStore,
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


def test_device_group_runtime_builds_account_level_snapshot() -> None:
    """测试目标：验证 SDK 设备组运行时可以按账号组织多设备。

    测试方法：
    1. 在同一账号下注册一副眼镜和一台手机。
    2. 绑定两个设备。
    3. 读取设备组运行态快照中的账号索引。

    预期结果：
    1. 账号快照包含两个设备和一个设备组。
    2. 账号快照记录眼镜与手机绑定关系。
    3. 业务代码可以通过 `query_account_devices` 读取账号下所有设备。
    """

    sdk = OpenAIGlassesSDK()
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass", account_id="acct_001", user_id="user_001")
    runtime.register_device(device_id="phone_001", role="phone", account_id="acct_001", user_id="user_001")

    group_id = runtime.bind_devices(glass_device_id="glass_001", phone_device_id="phone_001")
    snapshot = runtime.build_snapshot()

    account = snapshot["accounts"][0]
    assert account["account_id"] == "acct_001"
    assert account["user_id"] == "user_001"
    assert account["device_ids"] == ["glass_001", "phone_001"]
    assert account["group_ids"] == [group_id]
    assert account["online_device_count"] == 2
    assert account["bindings"] == [
        {
            "group_id": group_id,
            "glass_device_id": "glass_001",
            "phone_device_id": "phone_001",
        }
    ]
    assert [item.device_id for item in runtime.query_account_devices("acct_001")] == ["glass_001", "phone_001"]


def test_device_group_runtime_rejects_cross_account_binding() -> None:
    """测试目标：验证 SDK 不允许跨账号绑定眼镜和手机。

    测试方法：
    1. 分别在两个账号下注册眼镜和手机。
    2. 尝试绑定这两个设备。

    预期结果：
    1. 绑定被拒绝。
    2. 两个设备仍停留在各自账号和设备组内。
    """

    sdk = OpenAIGlassesSDK()
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass", account_id="acct_a")
    runtime.register_device(device_id="phone_001", role="phone", account_id="acct_b")

    with pytest.raises(RuntimeError, match="设备账号不一致"):
        runtime.bind_devices(glass_device_id="glass_001", phone_device_id="phone_001")

    snapshot = runtime.build_snapshot()
    accounts = {item["account_id"]: item for item in snapshot["accounts"]}
    assert accounts["acct_a"]["device_ids"] == ["glass_001"]
    assert accounts["acct_b"]["device_ids"] == ["phone_001"]
    assert snapshot["governance"]["recent_audit_events"][-1]["decision"] == "deny"
    assert snapshot["governance"]["recent_audit_events"][-1]["reason"] == "cross_account_binding"


def test_device_group_runtime_supports_organization_permission_and_audit() -> None:
    """测试目标：验证 SDK 账号治理支持组织树、角色权限和审计。

    测试方法：
    1. 注册同一账号下的眼镜和手机，并创建组织节点。
    2. 给开发者用户绑定账号级 developer 角色。
    3. 分别检查允许的 task.create 和拒绝的 config.write。

    预期结果：
    1. 组织节点进入运行态快照。
    2. 允许和拒绝都会生成审计事件。
    3. 权限拒绝会给出可解释原因。
    """

    sdk = OpenAIGlassesSDK()
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass", account_id="acct_001", user_id="owner_001")
    runtime.register_device(device_id="phone_001", role="phone", account_id="acct_001")
    group_id = runtime.bind_devices(glass_device_id="glass_001", phone_device_id="phone_001")
    runtime.create_organization_node(
        node_id="org_root",
        name="默认组织",
        account_ids={"acct_001"},
    )
    runtime.bind_role(
        subject_id="developer_001",
        role="developer",
        scope_type="account",
        scope_id="acct_001",
    )

    allowed = runtime.authorize(
        actor_id="developer_001",
        action="task.create",
        resource_type="device_group",
        resource_id=group_id,
        group_id=group_id,
    )
    denied = runtime.authorize(
        actor_id="developer_001",
        action="config.write",
        resource_type="device_group",
        resource_id=group_id,
        group_id=group_id,
    )
    snapshot = runtime.build_snapshot()

    assert allowed.allowed is True
    assert denied.allowed is False
    assert denied.reason == "no_matching_role"
    assert snapshot["governance"]["organization_nodes"][0]["node_id"] == "org_root"
    assert [event["decision"] for event in snapshot["governance"]["recent_audit_events"][-2:]] == [
        "allow",
        "deny",
    ]


def test_device_group_runtime_reads_scoped_remote_config() -> None:
    """测试目标：验证 SDK 远程配置 Provider 支持作用域优先级。

    测试方法：
    1. 配置全局、账号和设备级同名配置。
    2. 把 Provider 注入设备组运行时。
    3. 通过业务上下文读取配置。

    预期结果：
    1. 设备级配置优先于账号级配置。
    2. 缺失配置会返回默认值。
    3. 运行态快照包含配置版本。
    """

    sdk = OpenAIGlassesSDK()
    runtime = sdk.device_groups
    runtime.register_device(device_id="glass_001", role="glass", account_id="acct_001")
    provider = MemoryConfigProvider(version="test-v1")
    provider.set_value("sdk.playback.default_priority", "normal")
    provider.set_value(
        "sdk.playback.default_priority",
        "high",
        scope_type="account",
        scope_id="acct_001",
    )
    provider.set_value(
        "sdk.playback.default_priority",
        "critical",
        scope_type="device",
        scope_id="glass_001",
    )
    runtime.set_config_provider(provider)

    context = runtime.create_context(device_id="glass_001", session_id="sess_001")
    snapshot = runtime.build_snapshot()

    assert context.get_config("sdk.playback.default_priority") == "critical"
    assert context.get_config("missing.key", default="fallback") == "fallback"
    assert snapshot["governance"]["config"]["version"] == "test-v1"


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

    class MockMapAdapter(PublicBaseMcpAdapter):
        """用于测试的地图 MCP adapter。"""

        adapter_name = "mock_map"

        def list_methods(self) -> list[PublicMcpMethodSpec]:
            """返回测试方法清单。"""

            return [
                PublicMcpMethodSpec(
                    name="map.route_plan",
                    description="测试路线规划",
                    input_model=RouteInput,
                )
            ]

        def invoke(self, *, method_name: str, context, input_data) -> PublicCapabilityResult:
            """返回固定路线规划结果。"""

            return PublicCapabilityResult.success(
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


def test_phone_runtime_limits_vision_frames_by_interval() -> None:
    """测试目标：验证手机视觉运行时可以按最小帧间隔限制任务处理频率。

    测试方法：
    1. 注册一个记录帧序号的手机任务。
    2. 启动任务时传入 `vision_policy.min_frame_interval_ms`。
    3. 连续输入三帧，其中第二帧时间间隔不足。

    预期结果：
    1. 第一帧和第三帧被任务处理。
    2. 第二帧被 SDK 资源策略丢弃。
    3. 任务快照中记录 `vision.task.overloaded` 资源事件。
    """

    class CountingVisionTask(BasePhoneTask):
        task_type = "counting_vision_task"

        def on_start(self, context: PhoneTaskContext) -> None:
            context.emit_state("running")

        def on_frame(self, context: PhoneTaskContext, frame) -> None:
            context.emit_result({"seq": frame["seq"]})

    sdk = OpenAIGlassesSDK()
    sdk.register_phone_task(CountingVisionTask())
    snapshot = sdk.phone_runtime.start_task(
        task_type="counting_vision_task",
        params={
            "stream_id": "stream_cam_001",
            "vision_policy": {"min_frame_interval_ms": 1000},
        },
    )

    first = sdk.phone_runtime.process_frame(
        frame={"seq": 1},
        stream_id="stream_cam_001",
        now_ms=1000,
    )[0]
    second = sdk.phone_runtime.process_frame(
        frame={"seq": 2},
        stream_id="stream_cam_001",
        now_ms=1500,
    )[0]
    third = sdk.phone_runtime.process_frame(
        frame={"seq": 3},
        stream_id="stream_cam_001",
        now_ms=2200,
    )[0]

    assert snapshot.vision_policy["min_frame_interval_ms"] == 1000
    assert [item["seq"] for item in third.results] == [1, 3]
    assert first.frames_processed == 1
    assert second.frames_processed == 1
    assert second.frames_dropped == 1
    assert second.resource_events[-1]["event_name"] == "vision.task.overloaded"
    assert second.resource_events[-1]["reason"] == "frame_rate_limited"
    assert third.frames_processed == 2


def test_phone_runtime_records_vision_overload_when_max_frames_reached() -> None:
    """测试目标：验证手机视觉运行时可以在任务达到最大处理帧数后记录过载。

    测试方法：
    1. 注册一个简单手机任务。
    2. 启动任务时配置 `vision_policy.max_frames=1`。
    3. 连续向同一路视频流输入两帧。

    预期结果：
    1. 第一帧被处理。
    2. 第二帧被 SDK 丢弃并记录 `max_frames_reached`。
    3. 业务结果中不会出现第二帧，避免业务任务自行处理资源限制。
    """

    class MaxFrameVisionTask(BasePhoneTask):
        task_type = "max_frame_vision_task"

        def on_start(self, context: PhoneTaskContext) -> None:
            context.emit_state("running")

        def on_frame(self, context: PhoneTaskContext, frame) -> None:
            context.emit_result({"seq": frame["seq"]})

    sdk = OpenAIGlassesSDK()
    sdk.register_phone_task(MaxFrameVisionTask())
    sdk.phone_runtime.start_task(
        task_type="max_frame_vision_task",
        params={
            "stream_id": "stream_cam_002",
            "vision_policy": {"max_frames": 1},
        },
    )

    first = sdk.phone_runtime.process_frame(
        frame={"seq": 1},
        stream_id="stream_cam_002",
        now_ms=1000,
    )[0]
    second = sdk.phone_runtime.process_frame(
        frame={"seq": 2},
        stream_id="stream_cam_002",
        now_ms=2000,
    )[0]

    assert [item["seq"] for item in first.results] == [1]
    assert [item["seq"] for item in second.results] == [1]
    assert second.frames_processed == 1
    assert second.frames_dropped == 1
    assert second.resource_events[-1]["reason"] == "max_frames_reached"


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


def test_sdk_task_runtime_auto_persists_and_deduplicates_events(tmp_path) -> None:
    """测试目标：验证 SDK 任务运行时具备自动持久化和事件幂等能力。

    测试方法：
    1. 启用文件持久化后创建一个等待事件的任务。
    2. 使用相同 `event_id` 连续派发两次外部事件。
    3. 读取持久化文件并检查事件日志。

    预期结果：
    1. 创建任务和事件派发都会自动写入持久化文件。
    2. 相同 `event_id` 的事件只被处理一次。
    3. 文件采用带版本和保存时间的任务存储结构。
    """

    class IdempotentTask(BaseTask):
        task_type = "sdk_idempotent_task"

        def on_start(self, context) -> None:
            context.emit_state("running", {"count": 0})

        def on_event(self, context, event) -> None:
            context.update({"count": int(context.data.get("count") or 0) + 1})

    sdk = OpenAIGlassesSDK()
    sdk.register_task(IdempotentTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")
    store_file = tmp_path / "task-store.json"
    sdk.task_runtime.enable_persistence(store_file)

    created = sdk.task_runtime.create_task(
        task_type="sdk_idempotent_task",
        session_id="sess_idem_001",
        device_id="glass_001",
        input_data={},
    )
    first = sdk.task_runtime.dispatch_event(
        task_id=created.task_id,
        event_name="phone.demo.tick",
        payload={"event_id": "evt_tick_001"},
        source="phone",
    )
    second = sdk.task_runtime.dispatch_event(
        task_id=created.task_id,
        event_name="phone.demo.tick",
        payload={"event_id": "evt_tick_001"},
        source="phone",
    )

    payload = json.loads(store_file.read_text(encoding="utf-8"))
    assert payload["version"] == "sdk-task-store-v1"
    assert payload["tasks"][0]["task_id"] == created.task_id
    assert first.data["count"] == 1
    assert second.data["count"] == 1
    assert [event["event_id"] for event in second.events].count("evt_tick_001") == 1


def test_sdk_task_runtime_can_prune_terminal_tasks_from_persistence(tmp_path) -> None:
    """测试目标：验证终态任务可按保留期清理并同步持久化文件。

    测试方法：
    1. 创建一个立即完成的任务并启用持久化。
    2. 调用 `prune_tasks(retain_terminal_ms=0)`。
    3. 读取持久化文件。

    预期结果：
    1. 已完成任务被移除。
    2. 持久化文件中的任务列表同步变为空。
    """

    class CompletedTask(BaseTask):
        task_type = "sdk_completed_task"

        def on_start(self, context) -> None:
            context.complete({"ok": True})

    sdk = OpenAIGlassesSDK()
    sdk.register_task(CompletedTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")
    store_file = tmp_path / "task-store.json"
    sdk.task_runtime.enable_persistence(store_file)
    created = sdk.task_runtime.create_task(
        task_type="sdk_completed_task",
        session_id="sess_prune_001",
        device_id="glass_001",
        input_data={},
    )

    removed = sdk.task_runtime.prune_tasks(retain_terminal_ms=0, now_ms=created.completed_at_ms or 0)

    assert removed == [created.task_id]
    payload = json.loads(store_file.read_text(encoding="utf-8"))
    assert payload["tasks"] == []


def test_sdk_task_runtime_can_restore_from_sqlite_store(tmp_path) -> None:
    """测试目标：验证 SDK 托管任务可以通过 SQLite 文件恢复。

    测试方法：
    1. 启用 SQLite 持久化后创建运行中任务。
    2. 创建新的 SDK 运行时并从同一个 SQLite 文件恢复。
    3. 查询恢复后的任务状态。

    预期结果：
    1. SQLite 文件中保留任务快照。
    2. 新运行时可以恢复并查询原任务。
    3. 恢复事件会追加到任务事件日志。
    """

    class SQLitePersistedTask(BaseTask):
        task_type = "sdk_sqlite_persisted_task"

        def on_start(self, context) -> None:
            context.emit_state("running", {"phase": "sqlite"})

    db_path = tmp_path / "task-store.sqlite3"
    sdk = OpenAIGlassesSDK()
    sdk.register_task(SQLitePersistedTask())
    sdk.device_groups.register_device(device_id="glass_001", role="glass")
    sdk.task_runtime.enable_sqlite_persistence(db_path)
    created = sdk.task_runtime.create_task(
        task_type="sdk_sqlite_persisted_task",
        session_id="sess_sqlite_001",
        device_id="glass_001",
        input_data={"target": "demo"},
    )

    restored_sdk = OpenAIGlassesSDK()
    restored_sdk.register_task(SQLitePersistedTask())
    restored_sdk.device_groups.register_device(device_id="glass_001", role="glass")
    restored = restored_sdk.task_runtime.enable_sqlite_persistence(db_path, restore=True)
    latest = restored_sdk.task_runtime.query_task(created.task_id)

    assert restored[0].task_id == created.task_id
    assert latest.data["phase"] == "sqlite"
    assert latest.events[-1]["event_name"] == "task.restored"


def test_sqlite_task_store_keeps_event_idempotency_and_leases(tmp_path) -> None:
    """测试目标：验证 SQLite 存储具备事件幂等和租约能力。

    测试方法：
    1. 构造一个包含重复事件编号的任务快照并保存。
    2. 用两个 owner 访问同一个 SQLite 文件并竞争租约。
    3. 检查事件表和租约结果。

    预期结果：
    1. 重复事件只写入一条。
    2. 未过期租约不能被其他 owner 抢占。
    3. 过期后其他 owner 可以获得租约。
    """

    db_path = tmp_path / "task-store.sqlite3"
    store_a = SQLiteTaskPersistenceStore(db_path, owner_id="worker-a")
    snapshot = {
        "task_id": "task_sqlite_001",
        "task_type": "demo",
        "session_id": "sess_001",
        "device_id": "glass_001",
        "state": "running",
        "input_data": {},
        "data": {},
        "events": [
            {
                "event_id": "evt_same",
                "event_name": "phone.demo.tick",
                "state": "running",
                "source": "phone",
                "payload": {},
                "ts_ms": 1000,
            },
            {
                "event_id": "evt_same",
                "event_name": "phone.demo.tick",
                "state": "running",
                "source": "phone",
                "payload": {},
                "ts_ms": 1001,
            },
        ],
    }
    store_a.save([snapshot])
    store_b = SQLiteTaskPersistenceStore(db_path, owner_id="worker-b")

    first_lease = store_a.acquire_lease("task_sqlite_001", ttl_ms=1000, now_ms=1000)
    blocked_lease = store_b.acquire_lease("task_sqlite_001", ttl_ms=1000, now_ms=1200)
    expired_lease = store_b.acquire_lease("task_sqlite_001", ttl_ms=1000, now_ms=2101)

    with sqlite3.connect(db_path) as connection:
        event_count = connection.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]

    assert event_count == 1
    assert first_lease is True
    assert blocked_lease is False
    assert expired_lease is True
    assert store_b.list_leases()[0]["owner_id"] == "worker-b"


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


def test_build_agent_facade_from_sdk_preloads_agent_resources(monkeypatch) -> None:
    """测试目标：验证真实服务端装配阶段会预热 Agent 运行资源。"""

    calls: list[OpenAIAgentLoopRunner] = []

    def _fake_preload(self) -> None:
        calls.append(self)

    monkeypatch.setattr(OpenAIAgentLoopRunner, "preload_resources", _fake_preload)

    facade = build_agent_facade_from_sdk(
        sdk=OpenAIGlassesSDK(),
        settings=ServerSettings(dashscope_api_key="demo-key"),
    )

    assert len(calls) == 1
    assert facade.get_tool_registry().get("capture_photo") is not None


def test_sdk_tool_adapter_preserves_progress_message() -> None:
    """测试目标：验证业务 Tool 的前置播报文案会进入 agent-core。

    测试方法：
    1. 创建一个公开 SDK Tool，并设置 `progress_message`。
    2. 通过 SDK 构建 `AgentFacade`。
    3. 从内部工具注册表读取适配后的 ToolSpec。

    预期结果：
    1. 适配后的 Tool 保留业务声明的前置播报文案。
    2. 业务开发者不需要直接构造 agent-core 的 `ToolSpec`。
    """

    class DemoTool(BaseTool):
        name = "demo_progress_tool"
        description = "测试前置播报"
        progress_message = "我先处理一下。"

        def run(self, context, input_data):
            return PublicCapabilityResult.success(data={"ok": True})

    sdk = OpenAIGlassesSDK()
    sdk.register_tool(DemoTool())

    facade = build_agent_facade_from_sdk(
        sdk=sdk,
        settings=ServerSettings(),
    )

    tool = facade.get_tool_registry().get("demo_progress_tool")
    assert tool is not None
    assert tool.spec.progress_message == "我先处理一下。"


def test_openai_glasses_sdk_registers_skill_runtime() -> None:
    """测试目标：验证 OpenAIGlassesSDK 可注册 Skill 并注入 AgentFacade。

    测试方法：
    1. 创建 SDK 并注册一个 Skill 文档。
    2. 基于 SDK 构建 `AgentFacade`。
    3. 读取 facade 内部 Skill Runtime 快照和 `read_skill` 工具。

    预期结果：
    1. Skill 名称进入运行时快照。
    2. `read_skill` 工具被注册到 agent-core 工具表。
    """

    sdk = OpenAIGlassesSDK()
    sdk.register_skill(
        SkillDocument(
            manifest=SkillManifest(
                name="navigation_guide",
                version="1.0.0",
                description="导航引导 Skill",
                allowed_tools=[],
            ),
            content="根据导航任务上下文输出下一步提示。",
        )
    )

    facade = build_agent_facade_from_sdk(
        sdk=sdk,
        settings=ServerSettings(),
    )

    skill_runtime = facade.get_skill_runtime()
    assert skill_runtime is sdk.skill_runtime
    assert skill_runtime.build_snapshot()["registered_skill_names"] == ["navigation_guide"]
    assert facade.get_tool_registry().get("read_skill") is not None


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
        assert handle.runtime.device_group_runtime.video_link_start_adapter is not None
        assert handle.runtime.device_group_runtime.video_link_stop_adapter is not None
    finally:
        handle.server.server_close()


def test_openaiglasses_public_mcp_types_are_exported() -> None:
    """测试目标：验证业务 MCP Adapter 可只依赖 `openaiglasses` 公开入口。

    测试方法：
    1. 从 `openaiglasses` 导入公开 MCP 基类和方法描述类型。
    2. 与内部真实类型做身份比较。

    预期结果：
    1. 公开入口可直接用于业务侧 Mock MCP Adapter 类型声明。
    2. 不需要业务导入 `agent_core.mcp` 或 `agent_core.models`。
    """

    assert PublicBaseMcpAdapter is BaseMcpAdapter
    assert PublicMcpMethodSpec is McpMethodSpec


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
