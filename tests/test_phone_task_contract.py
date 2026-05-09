from __future__ import annotations

import json
from pathlib import Path

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.protocol import CONTROL_EVENTS, Event
from audio_chat.tasks import TaskRef


def test_phone_task_command_contract_uses_event_and_stream_semantics() -> None:
    """测试目标：验证 phone task 等价协议只使用公共 event + stream。

    测试方法：构造 `command.requested` 事件，检查 payload 只包含
    task 语义、输入参数和所需 stream，不包含点对点设备字段或媒体大字节。
    预期结果：事件可通过协议校验，且 progress/completed/failed 都是公共事件名。
    """

    assert "command.requested" in CONTROL_EVENTS
    assert "command.accepted" in CONTROL_EVENTS
    assert "command.progress" in CONTROL_EVENTS
    assert "command.completed" in CONTROL_EVENTS
    assert "command.failed" in CONTROL_EVENTS

    event = Event(
        event_name="command.requested",
        user_id="user-phone-task",
        producer_id="server-main",
        payload={
            "command": "phone.task.start",
            "task_type": "find_object_phone_task",
            "task_id": "task-find-object-001",
            "input": {"target": "水杯"},
            "required_streams": [{"stream_type": "sensor.rgb", "mode": "continuous", "format": "jpeg"}],
        },
    )

    data = event.to_dict()
    text = json.dumps(data, ensure_ascii=False)
    assert data["payload"]["task_type"] == "find_object_phone_task"
    assert "sensor.rgb" in text
    assert "target_device" not in text
    assert "target_device_id" not in text
    assert "image_base64" not in text


def test_phone_command_report_is_bridged_to_task_engine(tmp_path: Path) -> None:
    """测试目标：验证端侧 command 回报会进入 TaskEngine。

    测试方法：创建一个任务快照后，向 App 发布 `command.completed`。
    预期结果：任务进入 completed，runs 中写入 `phone_task.completed` 和 `task.completed`。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs")))
    created = TaskRef(
        task_id="task-phone-001",
        task_type="find_object_phone_task",
        state="running",
        metadata={"user_id": "user-phone-task", "session_id": "sess-phone-task", "input": {}},
    )
    app.task_engine.store.put(created)
    app.publish_control_event(
        Event(
            event_name="command.completed",
            user_id="user-phone-task",
            producer_id="dev-python-phone",
            payload={
                "task_id": "task-phone-001",
                "task_type": "find_object_phone_task",
                "result": {"found": True, "target": "水杯"},
                "summary": "找到水杯",
            },
        )
    )

    updated = app.task_engine.query("task-phone-001")
    assert updated.state == "completed"
    assert updated.summary == "找到水杯"
    task_events = (tmp_path / "runs/sessions/sess-phone-task/task-events.jsonl").read_text(encoding="utf-8")
    assert "phone_task.completed" in task_events
    assert "task.completed" in task_events
