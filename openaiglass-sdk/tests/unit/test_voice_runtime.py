"""VoiceRuntime 关键工具测试。"""

from __future__ import annotations

import base64
import struct
import unittest

from infra.config import ServerSettings
from runtime.voice_runtime import (
    MessageEntry,
    ModelChunk,
    PCM16StreamResampler,
    VoiceRuntime,
    VoiceSessionController,
    _extract_tts_event_summary,
    build_audio_data_url,
    extract_message_text,
    wav_header_unknown_size,
)


class VoiceRuntimeTestCase(unittest.TestCase):
    """验证 Phase C 语音运行时的基础工具与消息构造。

    主要功能：
    1. 校验音频重采样输出长度是否符合预期。
    2. 校验 WAV 头生成结果是否完整。
    3. 校验发给百炼 ASR 的音频输入格式是否符合 `data:` URL 约束。
    4. 校验对话模型的历史消息已经收敛为文本轮次。
    """

    def test_resampler_downsamples_24k_to_16k(self) -> None:
        """测试目标：验证 24k 音频能被压到 16k。

        测试方法：
        1. 构造 240 个 24k 单声道采样点。
        2. 调用流式重采样器执行最终收口。

        预期结果：
        1. 输出字节数为 320 字节，对应 160 个 16k 采样点。
        """

        samples = [1000] * 240
        pcm = struct.pack("<" + "h" * len(samples), *samples)
        resampler = PCM16StreamResampler(24000, 16000)

        converted = resampler.push(pcm, final=True)

        self.assertEqual(len(converted), 320)

    def test_wav_header_unknown_size_contains_riff_wave(self) -> None:
        """测试目标：验证未知长度 WAV 头的关键标识正确。

        测试方法：
        1. 生成 16k 单声道 WAV 头。
        2. 检查头部是否包含 `RIFF` 与 `WAVE` 标识。

        预期结果：
        1. 文件头前 4 字节为 `RIFF`。
        2. 第 8 到 12 字节为 `WAVE`。
        """

        header = wav_header_unknown_size(16000, 1)

        self.assertEqual(header[:4], b"RIFF")
        self.assertEqual(header[8:12], b"WAVE")

    def test_build_audio_data_url_uses_data_scheme(self) -> None:
        """测试目标：验证发给百炼 ASR 的音频输入使用 `data:` URL。

        测试方法：
        1. 构造一段最小 WAV 字节。
        2. 转成 `data:` URL。

        预期结果：
        1. 百炼请求中的音频字段是合法的 `data:` URL。
        2. `data:` URL 后半段仍然能还原出原始 WAV 字节。
        """

        input_wav = b"RIFFdemo"
        data_url = build_audio_data_url(input_wav)

        prefix = "data:audio/wav;base64,"
        self.assertTrue(data_url.startswith(prefix))
        encoded = data_url[len(prefix) :]
        self.assertEqual(base64.b64decode(encoded), input_wav)

    def test_build_model_messages_uses_text_history(self) -> None:
        """测试目标：验证历史消息以文本轮次进入模型请求。

        测试方法：
        1. 在会话控制器中放入一轮“用户文本 + 助手文本”历史。
        3. 重新构造新一轮模型消息。

        预期结果：
        1. 历史用户文本会进入 `messages`。
        2. 历史助手回复会以文本形式进入 `messages`。
        3. 当前轮用户文本仍位于最后一条消息。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
        )
        controller = VoiceSessionController(device_id="glass-001", device_type="glass", session_id="sess-test")
        controller.message_context.extend(
            [
                MessageEntry(
                    role="user",
                    kind="audio_input",
                    text="给我讲个笑话",
                ),
                MessageEntry(
                    role="assistant",
                    kind="assistant_reply",
                    text="这是上一轮回复",
                ),
            ]
        )

        messages = runtime._build_model_messages(controller, "我刚才问了你什么")  # noqa: SLF001 - 单测覆盖内部格式

        self.assertEqual(messages[1], {"role": "user", "content": "给我讲个笑话"})
        self.assertEqual(messages[2], {"role": "assistant", "content": "这是上一轮回复"})
        self.assertEqual(messages[3], {"role": "user", "content": "我刚才问了你什么"})

    def test_extract_message_text_reads_non_stream_response(self) -> None:
        """测试目标：验证非流式返回能正确提取文本。

        测试方法：
        1. 构造最小 completion 假对象。
        2. 调用文本提取函数。

        预期结果：
        1. 返回值等于假对象中的消息文本。
        """

        class _Message:
            content = "转写文本"

        class _Choice:
            message = _Message()

        class _Completion:
            choices = [_Choice()]

        self.assertEqual(extract_message_text(_Completion()), "转写文本")

    def test_extract_tts_event_summary_reads_sentence_end(self) -> None:
        """测试目标：验证 TTS 句子结束事件会被提炼成摘要。

        测试方法：
        1. 构造一条最小 `sentence-end` 事件字典。
        2. 调用事件摘要提取函数。

        预期结果：
        1. 返回值包含任务编号、句子序号、文本和字符数。
        """

        summary = _extract_tts_event_summary(
            {
                "header": {"task_id": "task-123", "event": "result-generated"},
                "payload": {
                    "output": {
                        "type": "sentence-end",
                        "sentence": {"index": 2},
                        "original_text": "第二句内容\n",
                        "usage": {"characters": 18},
                    }
                },
            }
        )

        self.assertEqual(
            summary,
            {
                "kind": "sentence-end",
                "task_id": "task-123",
                "sentence_index": 2,
                "text": "第二句内容",
                "characters": 18,
            },
        )

    def test_extract_tts_event_summary_reads_task_finished(self) -> None:
        """测试目标：验证 TTS 完成事件会被保留下来。

        测试方法：
        1. 构造一条 `task-finished` 事件字典。
        2. 调用事件摘要提取函数。

        预期结果：
        1. 返回值包含完成事件类型和任务编号。
        """

        summary = _extract_tts_event_summary(
            {
                "header": {"task_id": "task-456", "event": "task-finished"},
                "payload": {"output": {}},
            }
        )

        self.assertEqual(summary, {"kind": "task-finished", "task_id": "task-456"})

    def test_on_playback_finished_allows_old_stream_to_finish(self) -> None:
        """测试目标：验证旧播放流完成时不会误伤当前新播放流。

        测试方法：
        1. 手动创建同一会话下的两条播放流，后一条作为当前播放流。
        2. 先上报旧播放流的 `finished`。
        3. 再检查当前播放流是否仍然保留。

        预期结果：
        1. 旧播放流会被正常回收。
        2. 当前播放流不会因为旧流收尾被清空。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-test")

        first = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖内部状态机
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_old_001",
        )
        second = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖内部状态机
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_new_001",
        )

        runtime.on_playback_finished(
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_old_001",
        )

        snapshot = runtime.build_runtime_snapshot()
        self.assertTrue(first.completed)
        self.assertEqual(snapshot["glass-001"]["reply_stream_id"], second.stream_id)

    def test_on_playback_finished_ignores_late_interrupt_finish(self) -> None:
        """测试目标：验证被打断旧流的迟到完成回报会被忽略。

        测试方法：
        1. 构造一个最小运行时与会话。
        2. 预先登记一条已打断播放流标记。
        3. 直接上报该流的 `actuator.audio.finished`。

        预期结果：
        1. 运行时不会抛出找不到播放流异常。
        2. 已打断标记会在收到完成回报后被清理。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime._interrupted_playback_streams.add(("glass-001", "stream_interrupt"))  # noqa: SLF001 - 单测覆盖迟到回报路径

        runtime.on_playback_finished(
            device_id="glass-001",
            session_id="sess-test",
            stream_id="stream_interrupt",
        )

        self.assertNotIn(("glass-001", "stream_interrupt"), runtime._interrupted_playback_streams)  # noqa: SLF001 - 单测检查内部状态

    def test_on_playback_state_records_terminal_result(self) -> None:
        """测试目标：验证结构化播放终态会进入运行时快照。

        测试方法：
        1. 构造一个最小运行时并打开会话。
        2. 上报一条 `interrupted` 终态和原因。
        3. 读取运行时快照检查记录内容。

        预期结果：
        1. 快照中会保留最后一次播放流编号。
        2. 快照中会保留终态值与原因。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")

        runtime.on_playback_state(
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_state_001",
            state="interrupted",
            reason="interrupt_requested",
        )

        snapshot = runtime.build_runtime_snapshot()
        self.assertEqual(snapshot["glass-001"]["last_playback_stream_id"], "reply_state_001")
        self.assertEqual(snapshot["glass-001"]["last_playback_state"], "interrupted")
        self.assertEqual(snapshot["glass-001"]["last_playback_reason"], "interrupt_requested")

    def test_on_playback_finished_keeps_structured_terminal_state(self) -> None:
        """测试目标：验证收到 finished 后不会覆盖已记录的结构化终态。

        测试方法：
        1. 创建一条活动播放流。
        2. 先上报 `failed` 结构化终态。
        3. 再上报同一流的 `finished`。

        预期结果：
        1. 结构化终态仍保持为 `failed`。
        2. 原始原因不会被默认完成态覆盖。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖内部状态机
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_state_002",
        )

        runtime.on_playback_state(
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_state_002",
            state="failed",
            reason="speaker_write_failed",
        )
        runtime.on_playback_finished(
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_state_002",
        )

        snapshot = runtime.build_runtime_snapshot()
        self.assertEqual(snapshot["glass-001"]["last_playback_state"], "failed")
        self.assertEqual(snapshot["glass-001"]["last_playback_reason"], "speaker_write_failed")

    def test_create_playback_stream_queues_later_reply(self) -> None:
        """测试目标：验证后续回复播放流会排队等待当前播放流结束。

        测试方法：
        1. 创建同一会话下的两条播放流。
        2. 不结束第一条流，直接创建第二条流。
        3. 检查当前流与待播放队列状态。

        预期结果：
        1. 第一条流仍是当前播放流。
        2. 第二条流进入待播放队列，而不是覆盖当前流。
        """

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-queue")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-queue")

        first = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖内部状态机
            device_id="glass-001",
            session_id="sess-queue",
            stream_id="reply_queue_001",
        )
        second = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖内部状态机
            device_id="glass-001",
            session_id="sess-queue",
            stream_id="reply_queue_002",
        )

        snapshot = runtime.build_runtime_snapshot()
        self.assertEqual(snapshot["glass-001"]["reply_stream_id"], first.stream_id)
        controller = runtime._controllers["glass-001"]  # noqa: SLF001 - 单测覆盖内部状态机
        self.assertEqual(len(controller.pending_playbacks), 1)
        self.assertIs(controller.pending_playbacks[0], second)

    def test_high_priority_playback_interrupts_current_reply(self) -> None:
        """测试目标：验证高优先级视觉告警可以抢占普通 Agent 回复。

        测试方法：
        1. 创建一条普通 Agent 回复播放流。
        2. 再创建一条 critical 视觉告警播放流，并设置按优先级抢占。
        3. 检查运行态快照和下发给设备的中断控制消息。

        预期结果：
        1. 当前播放流切换为视觉告警。
        2. 原普通回复被标记为 interrupted。
        3. 控制面向设备发送 `actuator.audio.interrupt`。
        """

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args: sent_messages.append(args),
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-interrupt")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-interrupt")

        first = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖内部状态机
            device_id="glass-001",
            session_id="sess-interrupt",
            stream_id="reply_agent_001",
        )
        second = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖内部状态机
            device_id="glass-001",
            session_id="sess-interrupt",
            stream_id="reply_alert_001",
            source="vision_alert",
            priority="critical",
            interrupt_policy="higher_priority",
        )

        snapshot = runtime.build_runtime_snapshot()["glass-001"]
        self.assertTrue(first.failed)
        self.assertEqual(snapshot["reply_stream_id"], second.stream_id)
        self.assertEqual(snapshot["last_playback_state"], "interrupted")
        self.assertEqual(sent_messages[-1][2], "actuator.audio.interrupt")
        self.assertEqual(snapshot["recent_playback_decisions"][-1]["action"], "interrupt")

    def test_user_interrupt_clears_current_and_pending_playback(self) -> None:
        """测试目标：验证用户语音打断会清理当前播放和待播队列。

        测试方法：
        1. 创建当前播放流和一条待播播放流。
        2. 调用用户打断入口并要求清空队列。
        3. 检查播放流、快照和设备中断消息。

        预期结果：
        1. 当前播放流和待播播放流都从运行时移除。
        2. 快照中没有活动播放和待播播放。
        3. 运行时向设备发送 `actuator.audio.interrupt`。
        """

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args: sent_messages.append(args),
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-user-interrupt")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-user-interrupt")

        current = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖用户打断路径
            device_id="glass-001",
            session_id="sess-user-interrupt",
            stream_id="reply_user_interrupt_001",
        )
        pending = runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖用户打断路径
            device_id="glass-001",
            session_id="sess-user-interrupt",
            stream_id="reply_user_interrupt_002",
        )

        result = runtime.handle_user_interrupt(
            device_id="glass-001",
            session_id="sess-user-interrupt",
            reason="user_voice_interrupt",
            clear_queue=True,
        )

        snapshot = runtime.build_runtime_snapshot()["glass-001"]
        self.assertEqual(result["interrupted_stream_id"], current.stream_id)
        self.assertEqual(result["dropped_stream_ids"], [pending.stream_id])
        self.assertIsNone(snapshot["reply_stream_id"])
        self.assertEqual(snapshot["pending_playback_intents"], [])
        self.assertEqual(snapshot["last_playback_state"], "interrupted")
        self.assertEqual(sent_messages[-1][2], "actuator.audio.interrupt")
        self.assertEqual(snapshot["recent_playback_decisions"][-1]["action"], "user_interrupt")

    def test_stream_playback_treats_broken_pipe_as_client_disconnect(self) -> None:
        """测试目标：验证播放流 HTTP 客户端提前断开时不抛出 traceback。"""

        class _BrokenPipeHeaders:
            def write(self, _payload: bytes) -> None:
                raise BrokenPipeError("client closed")

        class _Handler:
            wfile = _BrokenPipeHeaders()

            def send_response(self, _status: int) -> None:
                return

            def send_header(self, _name: str, _value: str) -> None:
                return

            def end_headers(self) -> None:
                return

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime._create_playback_stream(  # noqa: SLF001 - 单测覆盖 HTTP 断流路径
            device_id="glass-001",
            session_id="sess-test",
            stream_id="reply_broken_pipe",
        )

        runtime.stream_playback(_Handler(), device_id="glass-001", stream_id="reply_broken_pipe")

    def test_reply_synthesis_snapshot_records_first_packet_latency(self) -> None:
        """测试目标：验证流式回复会记录首文本、首音频和首播放请求延迟。

        测试方法：
        1. 创建一条回复合成上下文。
        2. 标记首个文本增量。
        3. 注入一个最小音频分片触发播放请求。
        4. 读取运行时快照。

        预期结果：
        1. 快照中包含首文本、首音频、首播放请求时间戳。
        2. 快照中包含文本到首音频、首音频到播放请求的延迟字段。
        """

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args: sent_messages.append(args),
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-latency")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-latency")
        context = runtime._open_reply_synthesis_context(  # noqa: SLF001 - 单测覆盖首包观测字段
            device_id="glass-001",
            session_id="sess-latency",
        )

        runtime._mark_first_text_delta(context.playback)  # noqa: SLF001 - 单测覆盖首包观测字段
        runtime._emit_synthesis_chunk(  # noqa: SLF001 - 单测覆盖首包观测字段
            device_id="glass-001",
            session_id="sess-latency",
            context=context,
            chunk=ModelChunk(audio_pcm_bytes=b"\x01\x00" * 160, sample_rate_hz=16000),
        )

        snapshot = runtime.build_runtime_snapshot()["glass-001"]
        self.assertIsNotNone(snapshot["reply_first_text_delta_at_ms"])
        self.assertIsNotNone(snapshot["reply_first_audio_chunk_at_ms"])
        self.assertIsNotNone(snapshot["reply_first_play_request_at_ms"])
        self.assertIsNotNone(snapshot["reply_text_to_first_audio_ms"])
        self.assertIsNotNone(snapshot["reply_audio_to_play_request_ms"])
        self.assertEqual(sent_messages[-1][2], "actuator.audio.play")


if __name__ == "__main__":
    unittest.main()
