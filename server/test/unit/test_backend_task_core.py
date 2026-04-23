"""backend-task-core 单元测试。"""

from __future__ import annotations

import time
import unittest

from backend_task_core import InMemoryTaskGateway


class BackendTaskCoreTestCase(unittest.TestCase):
    """验证后台任务最小运行时。"""

    def setUp(self) -> None:
        """测试前创建新的任务网关。"""

        self.gateway = InMemoryTaskGateway()
        self.events = []
        self.gateway.subscribe_events(self.events.append)

    def tearDown(self) -> None:
        """测试后关闭后台定时器。"""

        self.gateway.shutdown()

    def test_timer_task_completes_and_publishes_event(self) -> None:
        """测试目标：验证计时器任务会自动完成并发布终态事件。

        测试方法：
        1. 创建一个 1 秒计时器任务。
        2. 轮询查询任务状态直到进入 `completed`。
        3. 检查事件列表中的创建、启动和完成事件。

        预期结果：
        1. 任务最终状态为 `completed`。
        2. 事件总线至少收到 `task.created`、`task.started`、`task.completed`。
        3. 完成事件允许直接通知端侧。
        """

        runtime = self.gateway.create_task(
            task_type="timer_task",
            session_id="sess_timer_001",
            device_id="glass-001",
            input_data={"duration_seconds": 1, "label": "泡面计时"},
        )

        deadline = time.time() + 2.5
        latest = runtime
        while time.time() < deadline:
            latest = self.gateway.query_task(runtime.task_id)
            if latest.state == "completed":
                break
            time.sleep(0.05)

        self.assertEqual(latest.state, "completed")
        event_names = [event.event_name for event in self.events]
        self.assertIn("task.created", event_names)
        self.assertIn("task.started", event_names)
        self.assertIn("task.completed", event_names)

        completed_event = next(event for event in self.events if event.event_name == "task.completed")
        self.assertTrue(completed_event.allow_direct_notify)
        self.assertEqual(completed_event.payload["message"], "计时结束了")

    def test_cancel_timer_task_prevents_late_completion(self) -> None:
        """测试目标：验证取消计时器后不会再收到完成事件。

        测试方法：
        1. 创建一个 1 秒计时器任务。
        2. 立即取消任务。
        3. 等待超过原始倒计时时间后检查事件列表。

        预期结果：
        1. 任务状态保持为 `cancelled`。
        2. 事件列表中不存在该任务的 `task.completed` 事件。
        """

        runtime = self.gateway.create_task(
            task_type="timer_task",
            session_id="sess_timer_002",
            device_id="glass-001",
            input_data={"duration_seconds": 1},
        )

        cancelled = self.gateway.cancel_task(runtime.task_id)
        self.assertEqual(cancelled.state, "cancelled")

        time.sleep(1.2)

        latest = self.gateway.query_task(runtime.task_id)
        self.assertEqual(latest.state, "cancelled")
        completed_events = [
            event for event in self.events if event.task_id == runtime.task_id and event.event_name == "task.completed"
        ]
        self.assertEqual(completed_events, [])

    def test_phone_video_link_task_starts_and_can_be_cancelled(self) -> None:
        """测试目标：验证视频直连任务可进入运行态并支持取消。

        测试方法：
        1. 创建一个 `phone_video_link_task`。
        2. 检查任务初始状态是否为 `running`。
        3. 再取消该任务并检查事件列表。

        预期结果：
        1. 创建后任务立即进入 `running`。
        2. 事件总线包含 `task.created`、`task.started`、`task.cancelled`。
        3. 取消后任务状态为 `cancelled`。
        """

        runtime = self.gateway.create_task(
            task_type="phone_video_link_task",
            session_id="sess_video_001",
            device_id="glass-001",
            input_data={
                "phone_device_id": "phone-001",
                "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
                "link_mode": "direct",
                "reason": "unit_test",
                "frame_interval_ms": 400,
            },
        )

        self.assertEqual(runtime.state, "running")
        self.assertEqual(runtime.context["phone_device_id"], "phone-001")
        self.assertEqual(runtime.context["target_ws_uri"], "ws://127.0.0.1:19001/ws/camera")

        cancelled = self.gateway.cancel_task(runtime.task_id)
        self.assertEqual(cancelled.state, "cancelled")

        event_names = [event.event_name for event in self.events if event.task_id == runtime.task_id]
        self.assertIn("task.created", event_names)
        self.assertIn("task.started", event_names)
        self.assertIn("task.cancelled", event_names)


if __name__ == "__main__":
    unittest.main()
