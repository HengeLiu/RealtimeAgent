"""backend-task-core 单元测试。"""

from __future__ import annotations

import unittest

from backend_task_core import InMemoryTaskGateway
from openaiglasses import OpenAIGlassesSDK
from openaiglasses.server import HybridTaskGateway


class BackendTaskCoreTestCase(unittest.TestCase):
    """验证后台任务最小运行时。"""

    def setUp(self) -> None:
        """测试前创建新的任务网关。"""

        self.gateway = HybridTaskGateway(
            base_gateway=InMemoryTaskGateway(),
            sdk_task_runtime=OpenAIGlassesSDK().task_runtime,
        )
        self.events = []
        self.gateway.subscribe_events(self.events.append)

    def tearDown(self) -> None:
        """测试后关闭后台定时器。"""

        self.gateway.shutdown()

    def test_base_gateway_no_longer_bundles_phone_video_link_task(self) -> None:
        """测试目标：验证根内存任务网关不再内建视频直连系统任务。

        测试方法：
        1. 直接使用 `InMemoryTaskGateway` 创建 `phone_video_link_task`。

        预期结果：
        1. 抛出 `TASK_NOT_FOUND`，提示该任务应由 SDK 集成层提供。
        """

        base_gateway = InMemoryTaskGateway()

        with self.assertRaisesRegex(Exception, "未找到对应任务模板"):
            base_gateway.create_task(
                task_type="phone_video_link_task",
                session_id="sess_video_000",
                device_id="glass-001",
                input_data={
                    "phone_device_id": "phone-001",
                    "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
                },
            )

    def test_hybrid_gateway_starts_and_cancels_phone_video_link_task(self) -> None:
        """测试目标：验证视频直连系统任务已改由 SDK 集成层托管。

        测试方法：
        1. 通过 `HybridTaskGateway` 创建一个 `phone_video_link_task`。
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
