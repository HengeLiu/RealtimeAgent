"""第二期 SDK 骨架测试。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from urllib.request import urlopen

import pytest

from server.main import create_sdk, create_server_handle
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
from openaiglasses.testing import ReplaySensorProvider, ReplayTimeline, ScenarioRunner


ROOT = Path(__file__).resolve().parents[3]
BLIND_APP_ROOT = ROOT / "openaiglass-for-blind"


def test_sdk_registry_can_load_blind_business_capability() -> None:
    """测试目标：验证盲人业务能力可注册到 SDK。

    测试方法：
    1. 调用 `server.main.create_sdk()` 创建 SDK。
    2. 检查 Tool、Task、PhoneProcessor 和 PhoneTask 注册表。

    预期结果：
    1. 注册表中包含 `start_find_object`、`find_object_task` 和 `yolo_find_object`。
    """

    sdk = create_sdk()

    assert sdk.registry.list_tool_names() == ["start_find_object"]
    assert sdk.registry.list_task_types() == ["find_object_task"]
    assert sdk.registry.list_phone_processor_types() == ["yolo_find_object"]
    assert sdk.registry.list_phone_task_types() == ["find_object_phone_task"]
    assert sdk.get_scenario_handler("find_object") is not None


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


def test_blind_business_replay_can_complete_find_object() -> None:
    """测试目标：验证盲人业务工程可以通过回放完成找物体闭环。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 使用 `ScenarioRunner` 读取 `testdata/scenario/find_object_basic.json`。

    预期结果：
    1. 任务进入 `completed`。
    2. 通知中包含找到目标的文本。
    """

    sdk = create_sdk()
    result = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).run(
        BLIND_APP_ROOT / "testdata/scenario/find_object_basic.json"
    )

    assert result["task_state"] == "completed"
    assert result["task_result"]["found"] is True
    assert any("找到水杯" in item["text"] for item in result["notifications"])
    assert result["assertions"]["passed"] is True
    assert "sensor.camera.stream.start" in [item["name"] for item in result["glass_commands"]]


def test_scenario_runner_can_describe_find_object_scenario_assets() -> None:
    """测试目标：验证场景回放器可以输出场景摘要。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 调用 `ScenarioRunner.describe(...)` 读取 testdata 场景摘要。

    预期结果：
    1. 摘要中包含 capability、资产路径和断言字段列表。
    """

    sdk = create_sdk()
    summary = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).describe(
        BLIND_APP_ROOT / "testdata/scenario/find_object_with_testdata.json"
    )

    assert summary["capability"] == "find_object"
    assert summary["scenario_id"] == "find_object_with_testdata"
    assert "task_state" in summary["expected_assertions"]
    assert any(item["usage"] == "frames" for item in summary["assets"])


def test_scenario_runner_can_validate_find_object_manifest() -> None:
    """测试目标：验证场景回放器可以校验正式场景 manifest。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 调用 `ScenarioRunner.validate(...)` 校验 testdata 场景。

    预期结果：
    1. 校验通过。
    2. 返回结果中包含场景摘要。
    """

    sdk = create_sdk()
    validation = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).validate(
        BLIND_APP_ROOT / "testdata/scenario/find_object_with_testdata.json"
    )

    assert validation["ok"] is True
    assert validation["errors"] == []
    assert validation["summary"]["scenario_id"] == "find_object_with_testdata"


def test_scenario_runner_can_validate_find_object_sensor_manifest() -> None:
    """测试目标：验证带传感器输入的找物体场景可以通过 manifest 校验。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 调用 `ScenarioRunner.validate(...)` 校验带 heading 传感器的场景。

    预期结果：
    1. 校验通过。
    2. 摘要中包含 `heading` 传感器类型和手机任务参数。
    """

    sdk = create_sdk()
    validation = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).validate(
        BLIND_APP_ROOT / "testdata/scenario/find_object_with_heading_sensor.json"
    )

    assert validation["ok"] is True
    assert validation["summary"]["replay_inputs"]["sensor_types"] == ["heading"]
    assert validation["summary"]["replay_inputs"]["phone_task_params"]["heading_sensor_type"] == "heading"


def test_scenario_runner_reports_missing_asset_in_validation(tmp_path: Path) -> None:
    """测试目标：验证场景校验会拦截缺失资产。

    测试方法：
    1. 在临时目录写入一个引用不存在帧资产的场景。
    2. 调用 `ScenarioRunner.validate(...)` 校验该场景。

    预期结果：
    1. 校验失败。
    2. 错误信息中包含找不到资产的提示。
    """

    scenario_path = tmp_path / "invalid_scenario.json"
    scenario_path.write_text(
        json.dumps(
            {
                "scenario_id": "invalid_find_object_missing_asset",
                "title": "无效找物体场景",
                "description": "引用了不存在的帧资产。",
                "capability": "find_object",
                "device_group": {
                    "glass": "mock_glass_001",
                    "phone": "mock_phone_001",
                },
                "inputs": {
                    "target_object": "水杯",
                    "frames": "text/not_exists.json",
                },
                "expected": {
                    "task_state": "completed",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    sdk = create_sdk()
    validation = ScenarioRunner(sdk, workspace_root=tmp_path).validate(scenario_path)

    assert validation["ok"] is False
    assert any("找不到场景资产文件" in item for item in validation["errors"])


def test_scenario_runner_can_load_frames_from_testdata_manifest() -> None:
    """测试目标：验证场景回放器可以从 testdata 目录解析复用资产。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 运行 `testdata/scenario/find_object_with_testdata.json`。

    预期结果：
    1. 回放结果满足 manifest 中的 expected 断言。
    2. 眼镜帧记录和手机侧结果记录均被保留下来。
    """

    sdk = create_sdk()
    result = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).run(
        BLIND_APP_ROOT / "testdata/scenario/find_object_with_testdata.json"
    )

    assert result["task_state"] == "completed"
    assert result["assertions"]["passed"] is True
    assert len(result["glass_frames"]) >= 2
    assert result["phone_results"][-1]["found"] is True


def test_scenario_runner_can_replay_find_object_with_heading_sensor() -> None:
    """测试目标：验证盲人业务回放可以消费 heading 传感器。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 运行 `testdata/scenario/find_object_with_heading_sensor.json`。

    预期结果：
    1. 任务进入 `completed`。
    2. 最终结构化结果中包含方向角字段。
    3. 传感器快照中记录了回放输入。
    """

    sdk = create_sdk()
    result = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).run(
        BLIND_APP_ROOT / "testdata/scenario/find_object_with_heading_sensor.json"
    )

    assert result["task_state"] == "completed"
    assert result["task_result"]["heading_degrees"] == 92
    assert result["phone_results"][-1]["heading_degrees"] == 92
    assert len(result["sensor_readings"]["heading"]) == 2
    assert result["assertions"]["passed"] is True


def test_scenario_runner_can_replay_cancelled_find_object_manifest() -> None:
    """测试目标：验证场景回放器可以覆盖取消路径并记录链路停止结果。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 运行 `testdata/scenario/find_object_cancelled.json`。

    预期结果：
    1. 任务进入 `cancelled`。
    2. 眼镜与手机命令记录中均包含停止链路的动作。
    """

    sdk = create_sdk()
    result = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).run(
        BLIND_APP_ROOT / "testdata/scenario/find_object_cancelled.json"
    )

    assert result["task_state"] == "cancelled"
    assert result["assertions"]["passed"] is True
    assert "sensor.camera.stream.stop" in [item["name"] for item in result["glass_commands"]]
    assert result["phone_stopped_tasks"]


def test_scenario_runner_can_report_missing_phone_failure() -> None:
    """测试目标：验证场景回放器可以表达缺少在线手机的失败场景。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 运行 `testdata/scenario/find_object_missing_phone.json`。

    预期结果：
    1. 任务进入 `failed`。
    2. 结果中包含 `task_start_failed` 结构化错误。
    """

    sdk = create_sdk()
    result = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).run(
        BLIND_APP_ROOT / "testdata/scenario/find_object_missing_phone.json"
    )

    assert result["task_state"] == "failed"
    assert result["task_error"]["code"] == "task_start_failed"
    assert result["assertions"]["passed"] is True


def test_scenario_runner_can_report_video_link_start_failure() -> None:
    """测试目标：验证场景回放器可以表达视频链路启动失败场景。

    测试方法：
    1. 创建已注册盲人业务能力的 SDK。
    2. 运行 `testdata/scenario/find_object_video_link_start_failed.json`。

    预期结果：
    1. 任务进入 `failed`。
    2. 回放结果中保留链路启动阶段的命令记录和结构化错误。
    """

    sdk = create_sdk()
    result = ScenarioRunner(sdk, workspace_root=BLIND_APP_ROOT).run(
        BLIND_APP_ROOT / "testdata/scenario/find_object_video_link_start_failed.json"
    )

    assert result["task_state"] == "failed"
    assert result["task_error"]["code"] == "task_start_failed"
    assert "sensor.camera.stream.start" in [item["name"] for item in result["glass_commands"]]
    assert result["assertions"]["passed"] is True


def test_replay_timeline_and_sensor_provider_can_work_together() -> None:
    """测试目标：验证回放时间轴和回放传感器提供者可以表达有时间序列的输入。

    测试方法：
    1. 构造一个包含两个 heading 事件的回放时间轴。
    2. 使用 `ReplaySensorProvider` 追加读数并顺序读取。

    预期结果：
    1. 时间轴事件按时间顺序输出。
    2. 传感器读数可按顺序读取，并在耗尽后返回最后一条。
    """

    timeline = ReplayTimeline.from_data(
        {
            "timeline_id": "heading_demo",
            "events": [
                {"at": 200, "type": "sensor.heading", "payload": {"heading_degrees": 92}},
                {"at": 50, "type": "sensor.heading", "payload": {"heading_degrees": 87}},
            ],
        }
    )

    assert [item.at for item in timeline.events] == [50, 200]

    provider = ReplaySensorProvider("heading")
    provider.append_reading({"heading_degrees": 87}, timestamp_ms=50)
    provider.append_reading({"heading_degrees": 92}, timestamp_ms=200)

    first = provider.read()
    second = provider.read()
    latest = provider.read()

    assert first.payload["heading_degrees"] == 87
    assert second.payload["heading_degrees"] == 92
    assert latest.payload["heading_degrees"] == 92


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
    1. 调用 `server.main.create_server_handle(...)`。
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
    1. 扫描 `sdk/python/openaiglasses` 下所有 Python 文件的 import 语句。
    2. 检查是否导入 `capabilities` 或盲人业务 `server` 入口。

    预期结果：
    1. SDK 源码中不存在对盲人业务代码的导入。
    """

    sdk_root = ROOT / "openaiglass-sdk/python/openaiglasses"
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
