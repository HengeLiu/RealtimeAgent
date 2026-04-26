"""SDK 公共契约金样测试。"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path

from openaiglasses import DeviceEndpoint, SensorReading, TaskRuntimeSnapshot
from openaiglasses.runtime import DeviceGroupRuntime
from protocol.messages.control_message import ControlMessage, Endpoint


ROOT = Path(__file__).resolve().parents[3]
CONTRACT_ROOT = ROOT / "testdata/contracts"


def _load_contract(name: str) -> dict:
    """读取一份契约金样。"""

    return json.loads((CONTRACT_ROOT / name).read_text(encoding="utf-8"))


def test_device_endpoint_contract_golden() -> None:
    """测试目标：验证设备端点公共对象金样保持稳定。

    测试方法：
    1. 构造一个 `DeviceEndpoint`。
    2. 使用 `dataclasses.asdict` 导出公共字段。
    3. 与 `testdata/contracts/device_endpoint.json` 对比。

    预期结果：
    1. 公共字段结构与金样一致。
    """

    endpoint = DeviceEndpoint(
        device_id="glass-001",
        role="glass",
        online=True,
        capabilities={"camera.capture", "camera.stream"},
        metadata={"firmware_version": "0.1.0"},
    )

    payload = asdict(endpoint)
    payload["capabilities"] = sorted(payload["capabilities"])
    assert payload == _load_contract("device_endpoint.json")


def test_control_message_contract_golden() -> None:
    """测试目标：验证控制消息公共信封金样保持稳定。

    测试方法：
    1. 构造一条 `sdk.phone.task.start` 控制消息。
    2. 调用 `to_dict()` 导出消息字典。
    3. 与 `testdata/contracts/control_message_v1.json` 对比。

    预期结果：
    1. 控制消息公共字段与金样一致。
    """

    message = ControlMessage(
        version="v1",
        message_id="msg_contract_001",
        trace_id="trace_contract_001",
        session_id="sess_contract_001",
        task_id="task_contract_001",
        stream_id="stream_contract_001",
        channel="control",
        semantic="request",
        name="sdk.phone.task.start",
        source=Endpoint(
            device_id="server-main",
            device_type="server",
            module="server-api",
        ),
        target=Endpoint(
            device_id="phone-001",
            device_type="phone",
            module="phone-host",
        ),
        priority="high",
        reply_to="msg_contract_previous",
        ts=1745539200000,
        payload={
            "task_id": "task_contract_001",
            "task_type": "find_object_phone_task",
            "stream_id": "stream_contract_001",
            "glass_device_id": "glass-001",
            "params": {
                "target_object": "water_cup",
                "processor_type": "yolo_find_object",
            },
        },
        meta={"contract": "sdk-v1"},
    )

    assert message.to_dict() == _load_contract("control_message_v1.json")


def test_control_message_contract_fixture_can_roundtrip() -> None:
    """测试目标：验证控制消息金样可被当前解析逻辑稳定读取。

    测试方法：
    1. 读取 `control_message_v1.json`。
    2. 调用 `ControlMessage.from_dict(...)`。
    3. 再次导出并比较结果。

    预期结果：
    1. 金样文件可稳定往返解析。
    """

    raw = _load_contract("control_message_v1.json")
    message = ControlMessage.from_dict(raw)
    assert message.to_dict() == raw


def test_task_runtime_snapshot_contract_golden() -> None:
    """测试目标：验证 SDK 任务快照公共对象金样保持稳定。

    测试方法：
    1. 构造 `TaskRuntimeSnapshot`。
    2. 使用 `dataclasses.asdict` 导出公共字段。
    3. 与 `testdata/contracts/task_runtime_snapshot.json` 对比。

    预期结果：
    1. 任务快照公共字段结构与金样一致。
    """

    snapshot = TaskRuntimeSnapshot(
        task_id="task_contract_001",
        task_type="find_object_task",
        session_id="sess_contract_001",
        device_id="glass-001",
        state="running",
        input_data={"target_object": "water_cup"},
        data={"processor_type": "yolo_find_object"},
        result=None,
        error=None,
    )

    assert asdict(snapshot) == _load_contract("task_runtime_snapshot.json")


def test_sensor_reading_contract_golden() -> None:
    """测试目标：验证传感器读数公共对象金样保持稳定。

    测试方法：
    1. 构造 `SensorReading`。
    2. 使用 `dataclasses.asdict` 导出公共字段。
    3. 与 `testdata/contracts/sensor_reading.json` 对比。

    预期结果：
    1. 传感器读数结构与金样一致。
    """

    reading = SensorReading(
        sensor_type="heading",
        payload={"heading_degrees": 90},
        timestamp_ms=1745539200000,
    )

    assert asdict(reading) == _load_contract("sensor_reading.json")


def test_phone_task_event_report_request_contract_golden() -> None:
    """测试目标：验证手机任务事件上报请求体金样保持稳定。

    测试方法：
    1. 构造当前手机任务事件上报请求体。
    2. 与 `testdata/contracts/task_event_report_request.json` 对比。

    预期结果：
    1. 统一事件上报请求体结构与金样一致。
    """

    payload = {
        "task_id": "task_contract_001",
        "phone_device_id": "phone-001",
        "event_name": "phone.vision.find_object.result",
        "payload": {
            "found": True,
            "summary": "found water_cup on the left",
        },
    }

    assert payload == _load_contract("task_event_report_request.json")


def test_phone_task_start_command_contract_golden() -> None:
    """测试目标：验证手机任务启动控制消息 payload 与当前公共契约一致。

    测试方法：
    1. 构造已绑定眼镜和手机的 `DeviceGroupRuntime`。
    2. 预置活动视频链路并注入假控制消息发送适配器。
    3. 调用 `start_phone_task(...)`。

    预期结果：
    1. 发送的消息名为 `sdk.phone.task.start`。
    2. payload 中保留 `task_id / task_type / stream_id / glass_device_id / params`。
    """

    runtime = DeviceGroupRuntime()
    runtime.register_device(device_id="glass-001", role="glass")
    runtime.register_device(device_id="phone-001", role="phone")
    group_id = runtime.bind_devices(glass_device_id="glass-001", phone_device_id="phone-001")
    runtime._active_video_links[group_id] = {"stream_id": "stream_contract_001"}  # noqa: SLF001

    sent: list[dict] = []
    runtime.device_command_adapter = lambda **kwargs: sent.append(kwargs) or {"ok": True, **kwargs}

    result = runtime.start_phone_task(
        group_id=group_id,
        session_id="sess_contract_001",
        sdk_task_id="task_contract_001",
        task_type="find_object_phone_task",
        params={
            "target_object": "water_cup",
            "processor_type": "yolo_find_object",
        },
    )

    assert result["name"] == "sdk.phone.task.start"
    assert result["payload"] == _load_contract("control_message_v1.json")["payload"]
    assert sent[0]["name"] == "sdk.phone.task.start"
    assert sent[0]["payload"] == _load_contract("control_message_v1.json")["payload"]


def test_phone_task_stop_command_contract_golden() -> None:
    """测试目标：验证手机任务停止控制消息 payload 与当前公共契约一致。

    测试方法：
    1. 构造已绑定眼镜和手机的 `DeviceGroupRuntime`。
    2. 注入假控制消息发送适配器。
    3. 调用 `stop_phone_task(...)`。

    预期结果：
    1. 发送的消息名为 `sdk.phone.task.stop`。
    2. payload 中保留 `task_id / task_type / reason`。
    """

    runtime = DeviceGroupRuntime()
    runtime.register_device(device_id="glass-001", role="glass")
    runtime.register_device(device_id="phone-001", role="phone")
    group_id = runtime.bind_devices(glass_device_id="glass-001", phone_device_id="phone-001")

    sent: list[dict] = []
    runtime.device_command_adapter = lambda **kwargs: sent.append(kwargs) or {"ok": True, **kwargs}

    result = runtime.stop_phone_task(
        group_id=group_id,
        session_id="sess_contract_001",
        sdk_task_id="task_contract_001",
        task_type="find_object_phone_task",
        reason="task.completed",
    )

    expected_payload = {
        "task_id": "task_contract_001",
        "task_type": "find_object_phone_task",
        "reason": "task.completed",
    }

    assert result["name"] == "sdk.phone.task.stop"
    assert result["payload"] == expected_payload
    assert sent[0]["name"] == "sdk.phone.task.stop"
    assert sent[0]["payload"] == expected_payload
