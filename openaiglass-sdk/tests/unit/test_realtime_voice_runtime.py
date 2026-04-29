"""全双工实时语音运行时测试。"""

from __future__ import annotations

import unittest

from infra.config import ServerSettings
from protocol.media import MediaFrame
from runtime.voice_runtime import VoiceRuntime


def _realtime_frame(
    *,
    session_id: str,
    input_stream_id: str,
    chunk_index: int = 0,
    voice_activity: str = "speech",
    barge_in_confidence: float = 0.9,
    echo_suppressed: bool = True,
) -> MediaFrame:
    """构造一帧实时语音媒体帧。

    参数：
    1. `session_id`：实时语音会话编号。
    2. `input_stream_id`：上行输入流编号。
    3. `chunk_index`：帧序号。
    4. `voice_activity`：端侧判断的人声活动类型。
    5. `barge_in_confidence`：端侧打断置信度。
    6. `echo_suppressed`：端侧是否已经执行回声抑制。

    返回值：
    1. 可直接传给 `VoiceRuntime.on_audio_frame(...)` 的媒体帧。
    """

    payload = b"\x01\x00" * 160
    return MediaFrame(
        header={
            "version": "v1",
            "session_id": session_id,
            "stream_id": input_stream_id,
            "input_stream_id": input_stream_id,
            "frame_type": "voice.realtime.input.delta",
            "seq": chunk_index,
            "chunk_index": chunk_index,
            "ts_ms": 1000 + chunk_index,
            "codec": "pcm16",
            "payload_size": len(payload),
            "final": False,
            "voice_activity": voice_activity,
            "barge_in_confidence": barge_in_confidence,
            "echo_suppressed": echo_suppressed,
        },
        payload=payload,
    )


class RealtimeVoiceRuntimeTestCase(unittest.TestCase):
    """验证 SDK 全双工实时语音第一版能力。

    主要功能：
    1. 覆盖实时会话打开、输入提交和输出分片。
    2. 覆盖用户插话进入播放仲裁器。
    3. 覆盖回声候选不会误触发用户打断。
    """

    def test_realtime_open_payload_exposes_semantic_vad_policy(self) -> None:
        """测试目标：验证服务端会把 semantic VAD 连续对话策略下发给 glass-esp32。

        测试方法：
        1. 用 `VOICE_CONVERSATION_MODE=realtime_semantic_vad` 创建 `VoiceRuntime`。
        2. 调用 `build_realtime_open_payload()`。
        3. 检查输入策略中的 turn detection 配置。

        预期结果：
        1. payload 标记当前会话为 `realtime_semantic_vad`。
        2. turn detection owner 为 `omni_realtime`。
        3. semantic VAD 阈值、静音时长和前置音频时长来自配置。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(
                voice_reply_mode="omni_realtime",
                voice_conversation_mode="realtime_semantic_vad",
                voice_realtime_semantic_vad_threshold=0.7,
                voice_realtime_silence_duration_ms=900,
                voice_realtime_prefix_padding_ms=320,
            ),
            send_control_message=lambda *_args: None,
        )

        payload = runtime.build_realtime_open_payload()

        self.assertEqual(payload["input"]["conversation_mode"], "realtime_semantic_vad")
        self.assertEqual(payload["input"]["turn_detection"]["owner"], "omni_realtime")
        self.assertEqual(payload["input"]["turn_detection"]["type"], "semantic_vad")
        self.assertEqual(payload["input"]["turn_detection"]["threshold"], 0.7)
        self.assertEqual(payload["input"]["turn_detection"]["silence_duration_ms"], 900)
        self.assertEqual(payload["input"]["turn_detection"]["prefix_padding_ms"], 320)

    def test_realtime_input_commit_emits_loopback_output_and_snapshot(self) -> None:
        """测试目标：验证全双工输入提交后能产生实时输出和快照。

        测试方法：
        1. 打开实时语音会话并确认端侧能力。
        2. 注入一帧实时上行音频。
        3. 提交输入流，使用 loopback 适配器生成下行输出。

        预期结果：
        1. 运行时向设备发送 `voice.realtime.output.delta`。
        2. 快照包含实时状态、输入流、输出流和首包延迟字段。
        """

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args: sent_messages.append(args),
        )

        runtime.open_realtime_session(device_id="glass-001", device_type="glass", session_id="sess-rt")
        runtime.on_realtime_session_opened(
            device_id="glass-001",
            session_id="sess-rt",
            payload={"accepted_mode": "full_duplex_realtime", "capabilities": {"aec": "endpoint"}},
        )
        runtime.on_realtime_input_started(
            device_id="glass-001",
            session_id="sess-rt",
            payload={"input_stream_id": "rt_in_001", "reason": "vad_speech"},
        )

        runtime.on_audio_frame(device_id="glass-001", frame=_realtime_frame(session_id="sess-rt", input_stream_id="rt_in_001"))
        runtime.on_realtime_input_committed(
            device_id="glass-001",
            session_id="sess-rt",
            payload={"input_stream_id": "rt_in_001", "final_transcript": "你好"},
        )

        snapshot = runtime.build_runtime_snapshot()["glass-001"]
        self.assertEqual(sent_messages[-1][2], "voice.realtime.output.delta")
        self.assertEqual(snapshot["realtime_state"], "playback_streaming")
        self.assertEqual(snapshot["active_realtime_input_stream_id"], "rt_in_001")
        self.assertEqual(snapshot["active_realtime_output_stream_id"], "rt_out_rt_in_001")
        self.assertIsNotNone(snapshot["realtime_latency_metrics"]["input_first_audio_ms"])
        self.assertIsNotNone(snapshot["active_playback_intent"])

    def test_realtime_user_interrupt_cancels_active_output(self) -> None:
        """测试目标：验证用户插话能取消当前实时输出。

        测试方法：
        1. 打开实时会话并主动下发一段实时输出。
        2. 上报 `voice.realtime.user_interrupt`。
        3. 再检查控制消息和运行态快照。

        预期结果：
        1. SDK 下发 `actuator.audio.interrupt` 和 `voice.realtime.output.cancelled`。
        2. 被取消输出流进入 `cancelled_output_stream_ids`。
        3. 播放仲裁器记录 `user_interrupt` 决策。
        """

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args: sent_messages.append(args),
        )
        runtime.open_realtime_session(device_id="glass-001", device_type="glass", session_id="sess-rt-int")
        runtime.on_realtime_session_opened(
            device_id="glass-001",
            session_id="sess-rt-int",
            payload={"capabilities": {"aec": "endpoint"}},
        )

        runtime._realtime_voice_runtime.emit_output_delta(  # noqa: SLF001 - 单测覆盖实时输出取消路径
            device_id="glass-001",
            session_id="sess-rt-int",
            output_stream_id="rt_out_001",
            text_delta="正在回答",
            audio_pcm_bytes=b"\x00\x00" * 160,
        )
        result = runtime.on_realtime_user_interrupt(
            device_id="glass-001",
            session_id="sess-rt-int",
            payload={"reason": "barge_in", "barge_in_confidence": 0.87, "clear_pending_playback": True},
        )

        names = [message[2] for message in sent_messages]
        snapshot = runtime.build_runtime_snapshot()["glass-001"]
        self.assertEqual(result["interrupted_stream_id"], "rt_out_001")
        self.assertIn("actuator.audio.interrupt", names)
        self.assertIn("voice.realtime.output.cancelled", names)
        self.assertIn("rt_out_001", snapshot["active_realtime_session"]["cancelled_output_stream_ids"])
        self.assertEqual(snapshot["recent_playback_decisions"][-1]["action"], "user_interrupt")

    def test_realtime_user_interrupt_aborts_half_duplex_playback_queue(self) -> None:
        """测试目标：验证实时插话能清理旧半双工本地播放队列。

        测试方法：
        1. 打开实时会话。
        2. 创建一条旧半双工 HTTP 播放流。
        3. 通过 `voice.realtime.user_interrupt` 触发用户插话。

        预期结果：
        1. 旧播放流的本地队列被中止。
        2. 运行态快照不再保留旧播放流。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args: None,
        )
        runtime.open_realtime_session(device_id="glass-001", device_type="glass", session_id="sess-bridge")
        runtime.on_realtime_session_opened(device_id="glass-001", session_id="sess-bridge", payload={})
        playback = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖实时插话桥接旧播放流
            device_id="glass-001",
            session_id="sess-bridge",
            stream_id="reply_half_duplex_001",
        )

        runtime.on_realtime_user_interrupt(
            device_id="glass-001",
            session_id="sess-bridge",
            payload={"reason": "barge_in"},
        )

        snapshot = runtime.build_runtime_snapshot()["glass-001"]
        self.assertTrue(playback.abort_event.is_set())
        self.assertIsNone(snapshot["reply_stream_id"])
        self.assertEqual(snapshot["last_playback_stream_id"], "reply_half_duplex_001")
        self.assertEqual(snapshot["last_playback_state"], "interrupted")

    def test_late_output_after_interrupt_is_dropped(self) -> None:
        """测试目标：验证用户打断后的迟到输出不会继续播放。

        测试方法：
        1. 创建实时输出并执行用户打断。
        2. 对同一输出流再次注入模型迟到分片。

        预期结果：
        1. 迟到分片返回 `dropped=True`。
        2. 不会再向设备发送新的实时输出分片。
        """

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args: sent_messages.append(args),
        )
        runtime.open_realtime_session(device_id="glass-001", device_type="glass", session_id="sess-late")
        runtime.on_realtime_session_opened(device_id="glass-001", session_id="sess-late", payload={})
        runtime._realtime_voice_runtime.emit_output_delta(  # noqa: SLF001
            device_id="glass-001",
            session_id="sess-late",
            output_stream_id="rt_out_late",
            text_delta="第一段",
            audio_pcm_bytes=b"\x00\x00" * 160,
        )
        runtime.on_realtime_user_interrupt(
            device_id="glass-001",
            session_id="sess-late",
            payload={"reason": "barge_in"},
        )
        before_count = len([item for item in sent_messages if item[2] == "voice.realtime.output.delta"])

        dropped = runtime._realtime_voice_runtime.emit_output_delta(  # noqa: SLF001
            device_id="glass-001",
            session_id="sess-late",
            output_stream_id="rt_out_late",
            text_delta="迟到分片",
            audio_pcm_bytes=b"\x00\x00" * 160,
        )
        after_count = len([item for item in sent_messages if item[2] == "voice.realtime.output.delta"])

        self.assertTrue(dropped["dropped"])
        self.assertEqual(before_count, after_count)

    def test_echo_candidate_is_recorded_but_not_interrupting(self) -> None:
        """测试目标：验证回声候选不会被 SDK 误当作用户打断。

        测试方法：
        1. 创建实时输出，使设备处于播放状态。
        2. 注入一帧 `voice_activity=echo` 的上行音频。
        3. 读取快照和播放仲裁决策。

        预期结果：
        1. `echo_rejected_count` 增加。
        2. 不会产生 `user_interrupt` 播放决策。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args: None,
        )
        runtime.open_realtime_session(device_id="glass-001", device_type="glass", session_id="sess-echo")
        runtime.on_realtime_session_opened(device_id="glass-001", session_id="sess-echo", payload={})
        runtime._realtime_voice_runtime.emit_output_delta(  # noqa: SLF001
            device_id="glass-001",
            session_id="sess-echo",
            output_stream_id="rt_out_echo",
            text_delta="播放中",
            audio_pcm_bytes=b"\x00\x00" * 160,
        )
        runtime.on_audio_frame(
            device_id="glass-001",
            frame=_realtime_frame(
                session_id="sess-echo",
                input_stream_id="rt_in_echo",
                voice_activity="echo",
                barge_in_confidence=0.2,
                echo_suppressed=False,
            ),
        )

        snapshot = runtime.build_runtime_snapshot()["glass-001"]
        actions = [item["action"] for item in snapshot["recent_playback_decisions"]]
        self.assertEqual(snapshot["realtime_echo_rejected_count"], 1)
        self.assertNotIn("user_interrupt", actions)

    def test_realtime_session_degrades_when_endpoint_aec_missing(self) -> None:
        """测试目标：验证端侧缺少 AEC 时结构化降级。

        测试方法：
        1. 打开实时语音会话。
        2. 端侧上报 `capabilities.aec=unsupported`。

        预期结果：
        1. 会话 `accepted_mode` 降为 `half_duplex`。
        2. 最近事件包含 `voice.realtime.session.degraded`。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args: None,
        )
        runtime.open_realtime_session(device_id="glass-001", device_type="glass", session_id="sess-degrade")
        runtime.on_realtime_session_opened(
            device_id="glass-001",
            session_id="sess-degrade",
            payload={"capabilities": {"aec": "unsupported"}},
        )

        snapshot = runtime.build_runtime_snapshot()["glass-001"]["active_realtime_session"]
        self.assertEqual(snapshot["accepted_mode"], "half_duplex")
        event_names = [event["name"] for event in snapshot["recent_realtime_events"]]
        self.assertIn("voice.realtime.session.degraded", event_names)


if __name__ == "__main__":
    unittest.main()
