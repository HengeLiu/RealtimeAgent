"""统一播放仲裁器单元测试。"""

from __future__ import annotations

import unittest

from runtime.playback_arbiter import PlaybackArbiter, PlaybackIntent


class PlaybackArbiterTestCase(unittest.TestCase):
    """验证普通回复、任务通知、视觉告警和用户打断的统一仲裁规则。"""

    def test_higher_priority_intent_interrupts_active_playback(self) -> None:
        """测试目标：验证高优先级播放意图会抢占普通播放。

        测试方法：
        1. 先提交一条普通 Agent 回复。
        2. 再提交一条允许按优先级抢占的视觉告警。
        3. 检查仲裁结果和快照中的活动意图。

        预期结果：
        1. 第二条意图的动作是 `interrupt`。
        2. 原活动播放被记录为被打断意图。
        3. 快照中活动播放切换到视觉告警。
        """

        arbiter = PlaybackArbiter()
        first = PlaybackIntent(
            intent_id="agent:reply_001",
            source="agent_reply",
            device_id="glass-001",
            session_id="sess-001",
            stream_id="reply_001",
            priority="normal",
            interrupt_policy="never",
        )
        second = PlaybackIntent(
            intent_id="vision:reply_002",
            source="vision_alert",
            device_id="glass-001",
            session_id="sess-001",
            stream_id="reply_002",
            priority="critical",
            interrupt_policy="higher_priority",
        )

        first_result = arbiter.submit(first)
        second_result = arbiter.submit(second)
        snapshot = arbiter.build_snapshot()

        self.assertEqual(first_result.decision.action, "play_now")
        self.assertEqual(second_result.decision.action, "interrupt")
        self.assertEqual(second_result.interrupted_intent, first)
        self.assertEqual(snapshot["active_intents"]["glass-001"]["stream_id"], "reply_002")

    def test_user_interrupt_drops_active_and_pending(self) -> None:
        """测试目标：验证用户主动打断会清理活动播放和待播队列。

        测试方法：
        1. 提交一条活动回复和一条排队回复。
        2. 调用用户打断接口并要求清空队列。
        3. 检查返回结果和运行态快照。

        预期结果：
        1. 活动回复被标记为被打断。
        2. 排队回复进入 dropped 列表。
        3. 快照不再包含活动或待播意图。
        """

        arbiter = PlaybackArbiter()
        active = PlaybackIntent(
            intent_id="agent:reply_001",
            source="agent_reply",
            device_id="glass-001",
            session_id="sess-001",
            stream_id="reply_001",
        )
        pending = PlaybackIntent(
            intent_id="agent:reply_002",
            source="agent_reply",
            device_id="glass-001",
            session_id="sess-001",
            stream_id="reply_002",
        )
        arbiter.submit(active)
        arbiter.submit(pending)

        result = arbiter.user_interrupt(
            device_id="glass-001",
            session_id="sess-001",
            reason="user_voice_interrupt",
            clear_queue=True,
        )
        snapshot = arbiter.build_snapshot()

        self.assertEqual(result.decision.action, "user_interrupt")
        self.assertEqual(result.interrupted_intent, active)
        self.assertEqual(result.dropped_intents, [pending])
        self.assertNotIn("glass-001", snapshot["active_intents"])
        self.assertNotIn("glass-001", snapshot["pending_intents"])


if __name__ == "__main__":
    unittest.main()
