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
        self.assertEqual(runtime.context["phase"], "peer_link_preparing")
        self.assertEqual(runtime.context["phone_device_id"], "phone-001")
        self.assertEqual(runtime.context["target_ws_uri"], "ws://127.0.0.1:19001/ws/camera")
        self.assertTrue(runtime.context["stream_id"].startswith("stream_"))

        cancelled = self.gateway.cancel_task(runtime.task_id)
        self.assertEqual(cancelled.state, "cancelled")
        self.assertEqual(cancelled.context["phase"], "cancelled")
        self.assertIs(self.gateway.cancel_task(runtime.task_id), cancelled)

        event_names = [event.event_name for event in self.events if event.task_id == runtime.task_id]
        self.assertIn("task.created", event_names)
        self.assertIn("task.started", event_names)
        self.assertIn("task.cancelled", event_names)

    def test_hybrid_gateway_dispatches_phone_video_link_lifecycle_events(self) -> None:
        """测试目标：验证系统视频任务可接收 peer-link 与视频流事件。

        测试方法：
        1. 创建 `phone_video_link_task`。
        2. 依次派发 `peer_link.ready`、`camera.stream.started`、`camera.stream.stopped`。

        预期结果：
        1. 任务阶段依次进入 `peer_link_ready`、`streaming`、`completed`。
        2. 事件总线能看到端侧事件与最终 `task.completed`。
        """

        runtime = self.gateway.create_task(
            task_type="phone_video_link_task",
            session_id="sess_video_002",
            device_id="glass-001",
            input_data={
                "phone_device_id": "phone-001",
                "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
            },
        )

        ready = self.gateway.dispatch_event(
            task_id=runtime.task_id,
            event_name="peer_link.ready",
            payload={"transport": "lan"},
            source="phone",
        )
        self.assertEqual(ready.state, "running")
        self.assertEqual(ready.context["phase"], "peer_link_ready")
        self.assertEqual(ready.context["last_peer_link_event"]["payload"]["transport"], "lan")

        streaming = self.gateway.dispatch_event(
            task_id=runtime.task_id,
            event_name="camera.stream.started",
            payload={"fps": 2},
            source="phone",
        )
        self.assertEqual(streaming.state, "running")
        self.assertEqual(streaming.context["phase"], "streaming")

        completed = self.gateway.dispatch_event(
            task_id=runtime.task_id,
            event_name="camera.stream.stopped",
            payload={"reason": "phone_closed"},
            source="phone",
        )
        self.assertEqual(completed.state, "completed")
        self.assertEqual(completed.context["phase"], "completed")
        self.assertEqual(completed.result["event_name"], "camera.stream.stopped")

        event_names = [event.event_name for event in self.events if event.task_id == runtime.task_id]
        self.assertIn("peer_link.ready", event_names)
        self.assertIn("camera.stream.started", event_names)
        self.assertIn("camera.stream.stopped", event_names)
        self.assertIn("task.completed", event_names)

    def test_hybrid_gateway_marks_phone_video_link_failed(self) -> None:
        """测试目标：验证 peer-link 失败事件会让系统视频任务进入失败态。

        测试方法：
        1. 创建 `phone_video_link_task`。
        2. 派发 `peer_link.failed` 并携带结构化原因。

        预期结果：
        1. 任务状态为 `failed`。
        2. `error` 和 `context.last_error` 保留端侧错误详情。
        """

        runtime = self.gateway.create_task(
            task_type="phone_video_link_task",
            session_id="sess_video_003",
            device_id="glass-001",
            input_data={
                "phone_device_id": "phone-001",
                "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
            },
        )

        failed = self.gateway.dispatch_event(
            task_id=runtime.task_id,
            event_name="peer_link.failed",
            payload={"reason": "phone_network_unreachable", "message": "手机网络不可达"},
            source="phone",
        )

        self.assertEqual(failed.state, "failed")
        self.assertEqual(failed.context["phase"], "failed")
        self.assertEqual(failed.error["code"], "peer_link_failed")
        self.assertEqual(failed.context["last_error"]["details"]["payload"]["reason"], "phone_network_unreachable")

    def test_hybrid_gateway_marks_phone_video_link_timeout_on_query(self) -> None:
        """测试目标：验证视频直连任务等待 peer-link 期间可进入超时态。

        测试方法：
        1. 创建 `phone_video_link_task`。
        2. 人为把 `deadline_at_ms` 调整到过去并查询任务。

        预期结果：
        1. 查询会把任务推进到 `timeout`。
        2. 事件总线发布 `task.timeout`。
        """

        runtime = self.gateway.create_task(
            task_type="phone_video_link_task",
            session_id="sess_video_004",
            device_id="glass-001",
            input_data={
                "phone_device_id": "phone-001",
                "target_ws_uri": "ws://127.0.0.1:19001/ws/camera",
            },
        )
        runtime.context["deadline_at_ms"] = 1

        timeout = self.gateway.query_task(runtime.task_id)

        self.assertEqual(timeout.state, "timeout")
        self.assertEqual(timeout.context["phase"], "timeout")
        self.assertEqual(timeout.error["code"], "peer_link_timeout")
        event_names = [event.event_name for event in self.events if event.task_id == runtime.task_id]
        self.assertIn("task.timeout", event_names)


if __name__ == "__main__":
    unittest.main()
