"""任务事件桥与通知协调单元测试。"""

from __future__ import annotations

import threading
import unittest

from agent_core.context import AgentSessionStore, AgentTurnResult
from backend_task_core import TaskEvent
from runtime.notifications import NotificationCoordinator, NotificationRequest, NotificationSubmitResult
from runtime.task_event_bridge import TaskEventBridge
from runtime.voice_runtime import VoiceRuntime
from infra.config import ServerSettings


class TaskEventRuntimeTestCase(unittest.TestCase):
    """验证任务事件桥与通知协调器。"""

    def test_task_event_bridge_writes_session_and_builds_request(self) -> None:
        """测试目标：验证任务事件会写入会话并转成通知申请。

        测试方法：
        1. 构造会话存储与任务事件桥。
        2. 提交一条允许直发的 `task.completed` 事件。
        3. 检查会话中的消息、派生结果和通知申请字段。

        预期结果：
        1. 会话中新增一条 `task_notification` 消息。
        2. 任务事件被保存为 `task_event` 派生结果。
        3. 返回的通知申请带有稳定的 `dedupe_key`。
        """

        session_store = AgentSessionStore()
        bridge = TaskEventBridge(session_store=session_store)

        request = bridge.handle_event(
            TaskEvent(
                event_id="evt_001",
                event_name="task.completed",
                task_id="task_001",
                task_type="example_task",
                session_id="sess_001",
                device_id="glass-001",
                state="completed",
                priority="high",
                requires_agent_decision=True,
                allow_direct_notify=True,
                ts=1234567890,
                payload={"message": "三分钟计时已结束"},
            )
        )

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request.dedupe_key, "task.completed:task_001")
        session = session_store.get_session("sess_001")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(len(session.messages), 1)
        self.assertEqual(session.messages[0].kind, "task_notification")
        self.assertEqual(len(session.artifacts), 1)
        artifact = next(iter(session.artifacts.values()))
        self.assertEqual(artifact.artifact_type, "task_event")

    def test_notification_coordinator_dedupes_same_request(self) -> None:
        """测试目标：验证通知协调器会裁掉重复通知。

        测试方法：
        1. 构造一个记录下发次数的假下发器。
        2. 连续提交两条 `dedupe_key` 相同的通知申请。
        3. 统计下发器被调用次数。

        预期结果：
        1. 第一条通知会被下发。
        2. 第二条通知会被去重裁掉。
        """

        dispatched: list[NotificationRequest] = []
        coordinator = NotificationCoordinator(dispatcher=dispatched.append)
        first = NotificationRequest(
            request_id="notify_001",
            source_module="backend-task-core",
            session_id="sess_001",
            device_id="glass-001",
            task_id="task_001",
            priority="high",
            notification_type="task.completed",
            delivery_mode="audio",
            allow_interrupt=True,
            allow_merge=False,
            requires_agent_context_sync=True,
            dedupe_key="task.completed:task_001",
            payload={"text": "三分钟计时已结束"},
        )
        second = NotificationRequest(
            request_id="notify_002",
            source_module="agent-core",
            session_id="sess_001",
            device_id="glass-001",
            task_id="task_001",
            priority="high",
            notification_type="task.completed",
            delivery_mode="audio",
            allow_interrupt=True,
            allow_merge=False,
            requires_agent_context_sync=True,
            dedupe_key="task.completed:task_001",
            payload={"text": "三分钟计时已结束"},
        )

        first_result = coordinator.submit(first)
        second_result = coordinator.submit(second)

        self.assertTrue(first_result.accepted)
        self.assertTrue(first_result.dispatched)
        self.assertFalse(second_result.accepted)
        self.assertEqual(len(dispatched), 1)

    def test_notification_coordinator_releases_next_request_after_completion(self) -> None:
        """测试目标：验证通知协调器会在当前通知完成后放行下一条。

        测试方法：
        1. 构造同一设备上的两条不同优先级通知。
        2. 先提交低优先级通知，再提交高优先级通知。
        3. 检查第二条通知在第一条完成前不会被下发。
        4. 调用完成接口后检查第二条通知被放行。

        预期结果：
        1. 第一条通知先进入下发器。
        2. 第二条通知进入队列，不会立刻下发。
        3. 第一条通知完成后，第二条通知自动下发。
        """

        dispatched: list[NotificationRequest] = []
        coordinator = NotificationCoordinator(dispatcher=dispatched.append)
        first = NotificationRequest(
            request_id="notify_101",
            source_module="backend-task-core",
            session_id="sess_101",
            device_id="glass-001",
            task_id="task_101",
            priority="normal",
            notification_type="task.progress.updated",
            delivery_mode="audio",
            allow_interrupt=False,
            allow_merge=True,
            requires_agent_context_sync=True,
            dedupe_key="task.progress.updated:task_101",
            payload={"text": "任务正在执行"},
        )
        second = NotificationRequest(
            request_id="notify_102",
            source_module="backend-task-core",
            session_id="sess_101",
            device_id="glass-001",
            task_id="task_102",
            priority="high",
            notification_type="task.completed",
            delivery_mode="audio",
            allow_interrupt=False,
            allow_merge=False,
            requires_agent_context_sync=True,
            dedupe_key="task.completed:task_102",
            payload={"text": "第二个任务完成"},
        )

        first_result = coordinator.submit(first)
        second_result = coordinator.submit(second)

        self.assertTrue(first_result.dispatched)
        self.assertTrue(second_result.accepted)
        self.assertTrue(second_result.queued)
        self.assertFalse(second_result.dispatched)
        self.assertEqual([request.request_id for request in dispatched], ["notify_101"])

        next_request = coordinator.complete_request(device_id="glass-001", request_id="notify_101")

        self.assertIsNotNone(next_request)
        assert next_request is not None
        self.assertEqual(next_request.request_id, "notify_102")
        self.assertEqual([request.request_id for request in dispatched], ["notify_101", "notify_102"])

    def test_notification_coordinator_interrupts_active_request_for_higher_priority(self) -> None:
        """测试目标：验证更高优先级通知会抢占当前活动通知。

        测试方法：
        1. 构造一条普通优先级活动通知和一条高优先级通知。
        2. 为协调器注入假中断器与假下发器。
        3. 先提交普通通知，再提交允许抢占的高优先级通知。

        预期结果：
        1. 高优先级通知会触发中断器。
        2. 高优先级通知会立刻进入下发器。
        3. 提交结果会标记本次发生了抢占。
        """

        dispatched: list[NotificationRequest] = []
        interrupted: list[NotificationRequest] = []
        coordinator = NotificationCoordinator(
            dispatcher=dispatched.append,
            interrupter=interrupted.append,
        )
        first = NotificationRequest(
            request_id="notify_201",
            source_module="backend-task-core",
            session_id="sess_201",
            device_id="glass-001",
            task_id="task_201",
            priority="normal",
            notification_type="task.progress.updated",
            delivery_mode="audio",
            allow_interrupt=False,
            allow_merge=True,
            requires_agent_context_sync=True,
            dedupe_key="task.progress.updated:task_201",
            payload={"text": "任务仍在执行"},
        )
        second = NotificationRequest(
            request_id="notify_202",
            source_module="backend-task-core",
            session_id="sess_201",
            device_id="glass-001",
            task_id="task_202",
            priority="critical",
            notification_type="task.alert",
            delivery_mode="audio",
            allow_interrupt=True,
            allow_merge=False,
            requires_agent_context_sync=True,
            dedupe_key="task.alert:task_202",
            payload={"text": "前方风险，请立即注意"},
        )

        coordinator.submit(first)
        second_result = coordinator.submit(second)

        self.assertEqual([request.request_id for request in interrupted], ["notify_201"])
        self.assertEqual([request.request_id for request in dispatched], ["notify_201", "notify_202"])
        self.assertTrue(second_result.accepted)
        self.assertTrue(second_result.dispatched)
        self.assertTrue(second_result.interrupted_active)

    def test_task_event_bridge_can_convert_event_to_agent_turn(self) -> None:
        """测试目标：验证任务事件可转换成标准 AgentTurn。

        测试方法：
        1. 构造任务事件桥。
        2. 调用 `convert_event_to_agent_turn`。
        3. 检查返回对象的来源、文本和派生结果。

        预期结果：
        1. 生成的 turn 来源为 `task_event`。
        2. turn 中包含任务事件摘要文本。
        3. turn 保留至少一个派生结果引用。
        """

        bridge = TaskEventBridge(session_store=AgentSessionStore())
        turn = bridge.convert_event_to_agent_turn(
            TaskEvent(
                event_id="evt_002",
                event_name="task.completed",
                task_id="task_002",
                task_type="example_task",
                session_id="sess_002",
                device_id="glass-001",
                state="completed",
                priority="normal",
                requires_agent_decision=True,
                allow_direct_notify=False,
                ts=1234567890,
                payload={"message": "计时结束"},
            )
        )

        self.assertEqual(turn.source, "task_event")
        self.assertIn("计时结束", turn.input_text)
        self.assertGreaterEqual(len(turn.derived_artifacts), 1)

    def test_voice_runtime_routes_task_event_into_agent_turn_when_required(self) -> None:
        """测试目标：验证需要回流决策的任务事件会进入 agent-core。

        测试方法：
        1. 构造带假 `AgentFacade` 的 `VoiceRuntime`。
        2. 提交一条 `requires_agent_decision=true` 且不允许直发的任务事件。
        3. 等待假门面收到回流的 `AgentTurn`，并检查通知申请。

        预期结果：
        1. `agent-core` 会收到一轮来源为 `task_event` 的输入。
        2. `VoiceRuntime` 会基于 agent 回复再生成一条通知申请。
        """

        class FakeAgentFacade:
            def __init__(self) -> None:
                self.session_store = AgentSessionStore()
                self.turns = []
                self.event = threading.Event()

            def get_session_store(self) -> AgentSessionStore:
                return self.session_store

            def handle_turn(self, turn):
                self.turns.append(turn)
                self.event.set()
                return AgentTurnResult(
                    turn_id=turn.turn_id,
                    session_id=turn.session_id,
                    device_id=turn.device_id,
                    reply_text="好的，我来提醒你。",
                )

        fake_facade = FakeAgentFacade()
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
            agent_facade=fake_facade,
        )
        submitted_requests: list[NotificationRequest] = []
        notification_submitted = threading.Event()

        def _submit_notification(request: NotificationRequest) -> NotificationSubmitResult:
            submitted_requests.append(request)
            notification_submitted.set()
            return NotificationSubmitResult(
                accepted=True,
                dispatched=False,
                queued=False,
            )

        runtime._notification_coordinator.submit = _submit_notification  # noqa: SLF001

        runtime.on_task_event(
            TaskEvent(
                event_id="evt_003",
                event_name="task.completed",
                task_id="task_003",
                task_type="example_task",
                session_id="sess_003",
                device_id="glass-001",
                state="completed",
                priority="normal",
                requires_agent_decision=True,
                allow_direct_notify=False,
                ts=1234567890,
                payload={"message": "计时结束"},
            )
        )

        self.assertTrue(fake_facade.event.wait(timeout=1.0))
        self.assertTrue(notification_submitted.wait(timeout=1.0))
        self.assertEqual(fake_facade.turns[0].source, "task_event")
        self.assertEqual(len(submitted_requests), 1)
        self.assertEqual(submitted_requests[0].source_module, "agent-core")
        self.assertEqual(submitted_requests[0].payload["text"], "好的，我来提醒你。")

    def test_voice_runtime_interrupt_notification_request_clears_old_stream(self) -> None:
        """测试目标：验证运行时会正确中断旧通知播放流。

        测试方法：
        1. 构造一个带活动通知播放流的 `VoiceRuntime`。
        2. 调用内部中断入口抢占当前通知。
        3. 检查旧播放流被标记终止且映射已清理。

        预期结果：
        1. 旧播放流会被设置为已终止。
        2. 当前活动播放流会被清空，等待新通知接管。
        3. 通知编号到播放流的映射会被移除。
        """

        sent_messages: list[tuple[str, str, str, str, dict[str, object]]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args, **_kwargs: sent_messages.append(args),
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess_301")
        playback = runtime._create_playback_stream(  # noqa: SLF001
            device_id="glass-001",
            session_id="sess_301",
            stream_id="reply_interrupt_001",
        )
        request = NotificationRequest(
            request_id="notify_301",
            source_module="backend-task-core",
            session_id="sess_301",
            device_id="glass-001",
            task_id="task_301",
            priority="normal",
            notification_type="task.progress.updated",
            delivery_mode="audio",
            allow_interrupt=False,
            allow_merge=True,
            requires_agent_context_sync=True,
            dedupe_key="task.progress.updated:task_301",
            payload={"text": "旧通知"},
        )
        runtime._notification_stream_requests[("glass-001", "reply_interrupt_001")] = "notify_301"  # noqa: SLF001
        runtime._notification_request_streams["notify_301"] = ("glass-001", "reply_interrupt_001")  # noqa: SLF001

        runtime._interrupt_notification_request(request)  # noqa: SLF001

        self.assertTrue(playback.abort_event.is_set())
        self.assertTrue(playback.completed)
        self.assertNotIn(("glass-001", "reply_interrupt_001"), runtime._playback_streams)  # noqa: SLF001
        self.assertNotIn("notify_301", runtime._notification_request_streams)  # noqa: SLF001
        self.assertIn(("glass-001", "reply_interrupt_001"), runtime._interrupted_playback_streams)  # noqa: SLF001
        controller = runtime._controllers["glass-001"]  # noqa: SLF001
        self.assertIsNone(controller.current_playback)
        self.assertEqual(len(sent_messages), 1)
        self.assertEqual(sent_messages[0][1], "request")
        self.assertEqual(sent_messages[0][2], "actuator.audio.interrupt")
        self.assertEqual(sent_messages[0][4]["stream_id"], "reply_interrupt_001")


if __name__ == "__main__":
    unittest.main()
