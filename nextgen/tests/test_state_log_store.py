"""状态日志存储测试。"""

from nextgen.apps.server.storage.state_log_store import StateLogStore


def test_state_log_store_can_keep_task_and_device_snapshots() -> None:
    """验证状态日志存储可以维护任务和设备快照。"""

    store = StateLogStore()
    store.append_task_event("tasksess_001", {"event_name": "created", "status": "created"})
    store.append_device_event("glass-001", {"event_name": "battery_low", "value": 15})

    task_snapshot = store.get_task_snapshot("tasksess_001")
    device_snapshot = store.get_device_snapshot("glass-001")

    assert task_snapshot is not None
    assert task_snapshot["last_event"]["event_name"] == "created"
    assert device_snapshot is not None
    assert device_snapshot["last_event"]["event_name"] == "battery_low"
