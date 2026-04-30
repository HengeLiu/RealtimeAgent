"""VoiceRuntime 关键工具测试。"""

from __future__ import annotations

import base64
import sys
import struct
import types
import unittest
from unittest.mock import patch

from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode
from protocol.media.media_frame import MediaFrame
from runtime.voice_runtime import (
    DashscopeRealtimeSpeechRecognitionSession,
    DashscopeCosyVoiceTtsSession,
    DashscopeOmniRealtimeReplyClient,
    MessageEntry,
    ModelChunk,
    OmniRealtimeReplyResult,
    PCM16StreamResampler,
    SegmentBuffer,
    StreamingSpeechRecognitionSession,
    VoiceRuntime,
    VoiceSessionController,
    _extract_recognition_sentence,
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

    def test_extract_recognition_sentence_reads_partial_and_final_text(self) -> None:
        """测试目标：验证官方 Recognition 实时 ASR 事件能提取文本和句尾标记。

        测试方法：
        1. 构造两个带 `get_sentence()` 的假回调结果。
        2. 一个只包含中间文本，一个包含 `end_time` 句尾字段。

        预期结果：
        1. 中间结果返回文本且 `is_sentence_end=False`。
        2. 句尾结果返回文本且 `is_sentence_end=True`。
        """

        class _Result:
            def __init__(self, sentence: dict[str, object]) -> None:
                self._sentence = sentence

            def get_sentence(self) -> dict[str, object]:
                return self._sentence

        partial_text, partial_end = _extract_recognition_sentence(_Result({"text": "看一下"}))
        final_text, final_end = _extract_recognition_sentence(_Result({"text": "看一下。", "end_time": 1280}))

        self.assertEqual(partial_text, "看一下")
        self.assertFalse(partial_end)
        self.assertEqual(final_text, "看一下。")
        self.assertTrue(final_end)

    def test_dashscope_tts_session_prewarms_stream_before_first_text(self) -> None:
        """测试目标：验证 CosyVoice TTS 会话会后台预启动流式任务。

        测试方法：
        1. 用假 DashScope SDK 替换真实 `SpeechSynthesizer`。
        2. 创建 `DashscopeCosyVoiceTtsSession` 并等待预热线程完成。
        3. 推送首个文本增量。

        预期结果：
        1. 预热线程会先调用一次内部 start-stream。
        2. 首个文本增量不会再次启动同一个流式任务。
        3. 文本仍会进入假 `SpeechSynthesizer.streaming_call(...)`。
        """

        class _AudioFormat:
            PCM_22050HZ_MONO_16BIT = "pcm"

        class _ResultCallback:
            pass

        class _SpeechSynthesizer:
            instances: list["_SpeechSynthesizer"] = []

            def __init__(self, *, model: str, voice: str, format: str, callback: _ResultCallback) -> None:
                self.model = model
                self.voice = voice
                self.format = format
                self.callback = callback
                self.start_count = 0
                self.streaming_calls: list[str] = []
                self._is_first = True
                _SpeechSynthesizer.instances.append(self)

            def __start_stream(self) -> None:
                self.start_count += 1
                self.callback.on_open()

            def streaming_call(self, text: str) -> None:
                if self._is_first:
                    self.__start_stream()
                    self._is_first = False
                self.streaming_calls.append(text)

            def streaming_complete(self) -> None:
                self.callback.on_complete()

        dashscope_module = types.ModuleType("dashscope")
        audio_module = types.ModuleType("dashscope.audio")
        tts_module = types.ModuleType("dashscope.audio.tts_v2")
        tts_module.AudioFormat = _AudioFormat
        tts_module.ResultCallback = _ResultCallback
        tts_module.SpeechSynthesizer = _SpeechSynthesizer

        with patch.dict(
            sys.modules,
            {
                "dashscope": dashscope_module,
                "dashscope.audio": audio_module,
                "dashscope.audio.tts_v2": tts_module,
            },
        ):
            session = DashscopeCosyVoiceTtsSession(
                settings=ServerSettings(dashscope_api_key="test-key"),
                on_chunk=lambda _chunk: None,
            )
            self.assertTrue(session._prewarm_done.wait(timeout=1.0))  # noqa: SLF001 - 单测验证预热状态

            synthesizer = _SpeechSynthesizer.instances[-1]
            self.assertEqual(synthesizer.start_count, 1)
            self.assertFalse(synthesizer._is_first)

            session.push_text("你")

        self.assertEqual(synthesizer.start_count, 1)
        self.assertEqual(synthesizer.streaming_calls, ["你"])

    def test_dashscope_omni_realtime_reply_client_streams_audio_delta(self) -> None:
        """测试目标：验证 Omni Realtime 音频增量会直接进入播放回调。

        测试方法：
        1. 注入假的 Omni Realtime 会话工厂。
        2. 在 `create_response()` 时模拟返回文本、音频和完成事件。
        3. 调用 `DashscopeOmniRealtimeReplyClient.run_reply(...)`。

        预期结果：
        1. SDK 会关闭服务端 VAD，手动提交音频并创建响应。
        2. `response.audio.delta` 会被解码成 `ModelChunk.audio_pcm_bytes`。
        3. 返回结果包含助手文本和用户语音转写。
        """

        class _Factory:
            instance: "_Conversation | None" = None

            def __call__(self, **kwargs):
                _Factory.instance = _Conversation(**kwargs)
                return _Factory.instance

        class _Conversation:
            def __init__(self, *, model: str, callback, url: str, api_key: str) -> None:
                self.model = model
                self.callback = callback
                self.url = url
                self.api_key = api_key
                self.updated: dict[str, object] = {}
                self.audio = ""
                self.video: list[str] = []
                self.events: list[str] = []
                self.committed = False
                self.closed = False

            def connect(self) -> None:
                self.callback.on_open()

            def update_session(self, **kwargs) -> None:
                self.updated = kwargs

            def append_audio(self, audio_b64: str) -> None:
                self.events.append("audio")
                self.audio = audio_b64

            def append_video(self, video_b64: str) -> None:
                self.events.append("video")
                self.video.append(video_b64)

            def commit(self) -> None:
                self.committed = True

            def create_response(self, **_kwargs) -> None:
                self.callback.on_event({"type": "response.created", "response": {"id": "resp-1"}})
                self.callback.on_event({"type": "response.audio_transcript.delta", "delta": "你好"})
                self.callback.on_event({"type": "response.audio.delta", "delta": base64.b64encode(b"pcm").decode()})
                self.callback.on_event(
                    {
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": "看一下",
                    }
                )
                self.callback.on_event({"type": "response.done"})

            def close(self) -> None:
                self.closed = True

        chunks: list[ModelChunk] = []
        client = DashscopeOmniRealtimeReplyClient(conversation_factory=_Factory())

        result = client.run_reply(
            settings=ServerSettings(
                dashscope_api_key="test-key",
                voice_omni_realtime_model_name="qwen3.5-omni-plus-realtime",
                voice_conversation_mode="segment_turn",
            ),
            input_pcm=b"\x01\x02",
            image_frames=[b"jpg"],
            instructions="简短回答",
            on_chunk=chunks.append,
            session_id="sess-test",
            device_id="glass-001",
            segment_id="seg-test",
            stream_id="stream-test",
        )

        assert _Factory.instance is not None
        self.assertFalse(_Factory.instance.updated["enable_turn_detection"])
        self.assertTrue(_Factory.instance.committed)
        self.assertEqual(_Factory.instance.events, ["audio", "video"])
        self.assertEqual(base64.b64decode(_Factory.instance.audio), b"\x01\x02")
        self.assertEqual([base64.b64decode(item) for item in _Factory.instance.video], [b"jpg"])
        self.assertTrue(_Factory.instance.closed)
        self.assertEqual(chunks[0].audio_pcm_bytes, b"pcm")
        self.assertEqual(chunks[0].sample_rate_hz, 24000)
        self.assertEqual(result.assistant_text, "你好")
        self.assertEqual(result.transcript, "看一下")
        self.assertEqual(result.response_id, "resp-1")

    def test_dashscope_omni_realtime_reply_client_enables_semantic_vad_when_configured(self) -> None:
        """测试目标：验证实验性连续对话模式会把 semantic VAD 参数写入 Omni session。

        测试方法：
        1. 注入假的 Omni Realtime 会话工厂。
        2. 使用 `VOICE_CONVERSATION_MODE=realtime_semantic_vad` 构造设置。
        3. 调用 `start_streaming_reply(...)` 并检查 `update_session(...)` 参数。

        预期结果：
        1. `enable_turn_detection=True`。
        2. turn detection 类型、阈值、静音时长和前置音频保留时长都来自配置。
        """

        class _Factory:
            instance: "_Conversation | None" = None

            def __call__(self, **kwargs):
                _Factory.instance = _Conversation(**kwargs)
                return _Factory.instance

        class _Conversation:
            def __init__(self, *, model: str, callback, url: str, api_key: str) -> None:
                self.callback = callback
                self.updated: dict[str, object] = {}
                self.closed = False

            def connect(self) -> None:
                self.callback.on_open()

            def update_session(self, **kwargs) -> None:
                self.updated = kwargs

            def close(self) -> None:
                self.closed = True

        client = DashscopeOmniRealtimeReplyClient(conversation_factory=_Factory())
        session = client.start_streaming_reply(
            settings=ServerSettings(
                dashscope_api_key="test-key",
                voice_reply_mode="omni_realtime",
                voice_conversation_mode="realtime_semantic_vad",
                voice_realtime_turn_detection_type="semantic_vad",
                voice_realtime_semantic_vad_threshold=0.72,
                voice_realtime_silence_duration_ms=900,
                voice_realtime_prefix_padding_ms=320,
            ),
            instructions="连续对话",
            on_chunk=lambda _chunk: None,
            session_id="sess-test",
            device_id="glass-001",
            segment_id="seg-test",
            stream_id="stream-test",
        )

        assert _Factory.instance is not None
        self.assertTrue(_Factory.instance.updated["enable_turn_detection"])
        self.assertEqual(_Factory.instance.updated["turn_detection_type"], "semantic_vad")
        self.assertEqual(_Factory.instance.updated["turn_detection_threshold"], 0.72)
        self.assertEqual(_Factory.instance.updated["turn_detection_silence_duration_ms"], 900)
        self.assertEqual(_Factory.instance.updated["prefix_padding_ms"], 320)
        session.close()
        self.assertTrue(_Factory.instance.closed)

    def test_omni_semantic_vad_waits_auto_response_without_manual_commit(self) -> None:
        """测试目标：验证 semantic VAD 模式不再手动提交 Omni 输入。

        测试方法：
        1. 注入假的 Omni Realtime 会话。
        2. 使用 `VOICE_CONVERSATION_MODE=realtime_semantic_vad` 创建流式会话。
        3. 追加音频和图片后，模拟 Omni 自动提交、返回音频和结束事件。
        4. 调用 `finish(...)` 等待自动响应。

        预期结果：
        1. SDK 不调用 `commit()`。
        2. SDK 不调用 `create_response(...)`。
        3. 模型音频仍通过回调进入播放链路。
        """

        class _Factory:
            instance: "_Conversation | None" = None

            def __call__(self, **kwargs):
                _Factory.instance = _Conversation(**kwargs)
                return _Factory.instance

        class _Conversation:
            def __init__(self, *, model: str, callback, url: str, api_key: str) -> None:
                self.callback = callback
                self.committed = False
                self.response_created = False
                self.images: list[str] = []

            def connect(self) -> None:
                self.callback.on_open()

            def update_session(self, **_kwargs) -> None:
                return None

            def append_audio(self, _audio_b64: str) -> None:
                return None

            def append_video(self, image_b64: str) -> None:
                self.images.append(image_b64)

            def commit(self) -> None:
                self.committed = True

            def create_response(self, **_kwargs) -> None:
                self.response_created = True

            def close(self) -> None:
                return None

        chunks: list[ModelChunk] = []
        client = DashscopeOmniRealtimeReplyClient(conversation_factory=_Factory())
        session = client.start_streaming_reply(
            settings=ServerSettings(
                dashscope_api_key="test-key",
                voice_reply_mode="omni_realtime",
                voice_conversation_mode="realtime_semantic_vad",
            ),
            instructions="连续对话",
            on_chunk=chunks.append,
            session_id="sess-test",
            device_id="glass-001",
            segment_id="seg-test",
            stream_id="stream-test",
        )
        session.append_audio(b"pcm")
        session.append_image_frames([b"jpeg"])
        assert _Factory.instance is not None
        _Factory.instance.callback.on_event({"type": "input_audio_buffer.speech_stopped"})
        _Factory.instance.callback.on_event(
            {"type": "response.audio.delta", "delta": base64.b64encode(b"audio").decode()}
        )
        _Factory.instance.callback.on_event({"type": "response.audio_transcript.delta", "delta": "你好"})
        _Factory.instance.callback.on_event(
            {"type": "conversation.item.input_audio_transcription.completed", "transcript": "看一下"}
        )
        _Factory.instance.callback.on_event({"type": "response.done"})

        result = session.finish(image_frames=[], instructions="连续对话", segment_finished_at_ms=0)

        self.assertFalse(_Factory.instance.committed)
        self.assertFalse(_Factory.instance.response_created)
        self.assertEqual(len(_Factory.instance.images), 1)
        self.assertEqual(chunks[0].audio_pcm_bytes, b"audio")
        self.assertEqual(result.assistant_text, "你好")
        self.assertEqual(result.transcript, "看一下")
        session.close()

    def test_omni_semantic_vad_reports_no_auto_response_for_reconnect_fallback(self) -> None:
        """测试目标：验证 semantic VAD 没有自动提交时不再原会话手动提交。

        测试方法：
        1. 注入不会主动发送 `speech_stopped` 的假 Omni Realtime 会话。
        2. 使用 `VOICE_CONVERSATION_MODE=realtime_semantic_vad` 创建流式会话。
        3. 追加音频后直接调用 `finish(...)`。

        预期结果：
        1. SDK 不在 semantic VAD 会话中手动 `commit()`。
        2. SDK 抛出可识别的兜底信号，外层可以重连 `segment_turn` 模式。
        """

        class _Factory:
            instance: "_Conversation | None" = None

            def __call__(self, **kwargs):
                _Factory.instance = _Conversation(**kwargs)
                return _Factory.instance

        class _Conversation:
            def __init__(self, *, model: str, callback, url: str, api_key: str) -> None:
                self.callback = callback
                self.committed = False
                self.response_created = False

            def connect(self) -> None:
                self.callback.on_open()

            def update_session(self, **_kwargs) -> None:
                return None

            def append_audio(self, _audio_b64: str) -> None:
                return None

            def append_video(self, _image_b64: str) -> None:
                return None

            def commit(self) -> None:
                self.committed = True

            def create_response(self, **_kwargs) -> None:
                self.response_created = True

            def close(self) -> None:
                return None

        client = DashscopeOmniRealtimeReplyClient(conversation_factory=_Factory())
        session = client.start_streaming_reply(
            settings=ServerSettings(
                dashscope_api_key="test-key",
                voice_reply_mode="omni_realtime",
                voice_conversation_mode="realtime_semantic_vad",
            ),
            instructions="连续对话",
            on_chunk=lambda _chunk: None,
            session_id="sess-test",
            device_id="glass-001",
            segment_id="seg-test",
            stream_id="stream-test",
        )
        session.append_audio(b"pcm")

        with self.assertRaises(AppError) as captured:
            session.finish(image_frames=[], instructions="连续对话", segment_finished_at_ms=100)

        assert _Factory.instance is not None
        self.assertFalse(_Factory.instance.committed)
        self.assertFalse(_Factory.instance.response_created)
        self.assertEqual(captured.exception.code, ErrorCode.TIMEOUT)
        self.assertEqual(captured.exception.details["reason"], "semantic_vad_no_auto_response")
        self.assertEqual(captured.exception.details["fallback"], "segment_turn_reconnect")
        session.close()

    def test_omni_segment_turn_fallback_disables_semantic_vad(self) -> None:
        """测试目标：验证 semantic VAD 兜底会改用分段提交模式。

        测试方法：
        1. 注入假的 Omni Realtime 客户端，记录收到的配置。
        2. 使用 `realtime_semantic_vad` 默认配置创建运行时。
        3. 调用内部兜底方法并注入一段模型音频。

        预期结果：
        1. 兜底请求里的 `voice_conversation_mode` 被改成 `segment_turn`。
        2. 模型音频仍进入下行播放流，复用原播放链路。
        """

        class _OmniClient:
            seen_mode: str | None = None

            def run_reply(self, *, settings, input_pcm, image_frames, instructions, on_chunk, **_kwargs):
                self.seen_mode = settings.voice_conversation_mode
                self.assert_input = input_pcm
                self.assert_images = image_frames
                self.assert_instructions = instructions
                on_chunk(ModelChunk(audio_pcm_bytes=b"\x01\x00" * 160, sample_rate_hz=16000))
                return OmniRealtimeReplyResult(
                    assistant_text="收到",
                    transcript="继续说",
                    response_id="resp-fallback",
                )

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        client = _OmniClient()
        runtime = VoiceRuntime(
            settings=ServerSettings(
                dashscope_api_key="test-key",
                voice_reply_mode="omni_realtime",
                voice_conversation_mode="realtime_semantic_vad",
            ),
            send_control_message=lambda *args: sent_messages.append(args),
            omni_realtime_client=client,  # type: ignore[arg-type]
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-fallback")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-fallback")
        segment = SegmentBuffer(
            session_id="sess-fallback",
            stream_id="stream-fallback",
            segment_id="seg-fallback",
            sample_rate=16000,
            channels=1,
            codec="pcm16",
            started_at_ms=0,
        )
        context = runtime._open_reply_synthesis_context(  # noqa: SLF001 - 单测覆盖兜底播放链路
            device_id="glass-001",
            session_id="sess-fallback",
            audio_source="omni_realtime",
        )

        result = runtime._run_omni_segment_turn_fallback(  # noqa: SLF001 - 单测覆盖兜底模式切换
            device_id="glass-001",
            session_id="sess-fallback",
            segment=segment,
            input_pcm=b"pcm",
            image_frames=[b"jpeg"],
            instructions="继续对话",
            context=context,
        )

        self.assertEqual(client.seen_mode, "segment_turn")
        self.assertEqual(result.response_id, "resp-fallback")
        self.assertEqual(sent_messages[-1][2], "actuator.audio.play")

    def test_dashscope_realtime_asr_sends_chunks_to_recognition(self) -> None:
        """测试目标：验证实时 ASR 使用官方 Recognition 会话逐帧发送音频。

        测试方法：
        1. 用假 `Recognition` 替换 dashscope SDK 中的真实类。
        2. 创建实时 ASR session 并追加一个 PCM 音频分片。
        3. 手动触发中间识别、句尾识别和完成回调。

        预期结果：
        1. SDK 会调用 `Recognition.start()`。
        2. 音频分片会立即进入 `send_audio_frame(...)`。
        3. `finish()` 返回句尾文本，延迟指标来自首个音频分片和回调事件。
        """

        class _Result:
            def __init__(self, sentence: dict[str, object]) -> None:
                self._sentence = sentence

            def get_sentence(self) -> dict[str, object]:
                return self._sentence

        class _Recognition:
            instance: "_Recognition | None" = None

            def __init__(self, *, model: str, callback, format: str, sample_rate: int, **kwargs) -> None:
                self.model = model
                self.callback = callback
                self.format = format
                self.sample_rate = sample_rate
                self.kwargs = kwargs
                self.started = False
                self.stopped = False
                self.frames: list[bytes] = []
                _Recognition.instance = self

            def start(self) -> None:
                self.started = True
                self.callback.on_open()

            def send_audio_frame(self, frame: bytes) -> None:
                self.frames.append(frame)

            def stop(self) -> None:
                self.stopped = True
                self.callback.on_complete()

        settings = ServerSettings(dashscope_api_key="demo-key", voice_asr_realtime_model_name="fun-asr-realtime")
        with patch("dashscope.audio.asr.Recognition", _Recognition):
            session = DashscopeRealtimeSpeechRecognitionSession(
                settings=settings,
                session_id="sess-test",
                device_id="glass-001",
                segment_id="seg-test",
                stream_id="stream-test",
                sample_rate_hz=16000,
            )
            session.append_audio(b"\x01\x02")
            assert _Recognition.instance is not None
            _Recognition.instance.callback.on_event(_Result({"text": "看一下"}))
            _Recognition.instance.callback.on_event(_Result({"text": "看一下。", "end_time": 1200}))

            text = session.finish()

        assert _Recognition.instance is not None
        self.assertTrue(_Recognition.instance.started)
        self.assertTrue(_Recognition.instance.stopped)
        self.assertEqual(_Recognition.instance.model, "fun-asr-realtime")
        self.assertEqual(_Recognition.instance.format, "pcm")
        self.assertEqual(_Recognition.instance.sample_rate, 16000)
        self.assertEqual(_Recognition.instance.kwargs["max_sentence_silence"], 300)
        self.assertEqual(_Recognition.instance.frames, [b"\x01\x02"])
        self.assertEqual(text, "看一下。")
        self.assertIsNotNone(session.metrics()["first_asr_partial_latency_ms"])
        self.assertIsNotNone(session.metrics()["asr_total_latency_ms"])

    def test_transcribe_segment_prefers_streaming_asr_result(self) -> None:
        """测试目标：验证语音段优先使用实时 ASR 已完成的文本。

        测试方法：
        1. 构造一个带假实时 ASR 会话的 `SegmentBuffer`。
        2. 让批量 ASR 客户端在被调用时抛错。
        3. 调用 `_transcribe_segment`。

        预期结果：
        1. 返回实时 ASR 文本。
        2. 不会再调用批量 ASR。
        """

        class _StreamingSession(StreamingSpeechRecognitionSession):
            def append_audio(self, pcm_bytes: bytes) -> None:
                return None

            def finish(self) -> str:
                return "实时转写文本"

            def metrics(self) -> dict[str, int | None]:
                return {
                    "first_audio_chunk_at_ms": 1000,
                    "first_asr_partial_latency_ms": 120,
                    "asr_total_latency_ms": 280,
                }

        class _BatchAsrShouldNotRun:
            def transcribe(self, *, settings: ServerSettings, input_wav: bytes) -> str:
                raise AssertionError("batch ASR should not be called")

        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *_args, **_kwargs: None,
            asr_client=_BatchAsrShouldNotRun(),
        )
        segment = SegmentBuffer(
            session_id="sess-test",
            stream_id="stream-test",
            segment_id="seg-test",
            sample_rate=16000,
            channels=1,
            codec="pcm16",
            started_at_ms=0,
            streaming_asr_session=_StreamingSession(),
        )

        with self.assertLogs("server.voice", level="INFO") as logs:
            text = runtime._transcribe_segment(  # noqa: SLF001 - 单测覆盖实时 ASR 优先级
                device_id="glass-001",
                session_id="sess-test",
                segment=segment,
                input_wav=b"RIFFdemo",
            )

        self.assertEqual(text, "实时转写文本")
        self.assertIn("first_asr_partial_latency_ms=120", "\n".join(logs.output))
        self.assertIn("asr_total_latency_ms=280", "\n".join(logs.output))

    def test_on_segment_started_skips_realtime_asr_in_omni_mode(self) -> None:
        """测试目标：验证 Omni Realtime 直出模式不会启动独立实时 ASR。

        测试方法：
        1. 构造一个调用即失败的假 ASR 客户端。
        2. 将 `voice_reply_mode` 设置为 `omni_realtime`。
        3. 打开语音会话并上报 `sensor.audio.segment.started`。

        预期结果：
        1. `on_segment_started` 不会调用假 ASR 客户端。
        2. 当前语音段仍正常进入接收状态。
        3. 当前语音段不持有实时 ASR 会话。
        """

        class _AsrShouldNotStart:
            def start_streaming_session(self, **_kwargs):
                raise AssertionError("streaming ASR should not start in omni_realtime mode")

        runtime = VoiceRuntime(
            settings=ServerSettings(voice_reply_mode="omni_realtime"),
            send_control_message=lambda *_args, **_kwargs: None,
            asr_client=_AsrShouldNotStart(),
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-test")

        runtime.on_segment_started(
            device_id="glass-001",
            session_id="sess-test",
            payload={
                "stream_id": "stream-test",
                "segment_id": "seg-test",
                "sample_rate": 16000,
                "channels": 1,
                "codec": "pcm16",
            },
        )

        controller = runtime._controllers["glass-001"]  # noqa: SLF001 - 单测检查运行时内部状态
        self.assertEqual(controller.state, "receiving_segment")
        assert controller.current_segment is not None
        self.assertIsNone(controller.current_segment.streaming_asr_session)

    def test_omni_mode_prestreams_audio_frames(self) -> None:
        """测试目标：验证 Omni 模式会在录音过程中预连接并推送音频。

        测试方法：
        1. 注入假的 Omni Realtime 客户端和会话。
        2. 上报 `sensor.audio.segment.started`。
        3. 模拟一帧 `/ws_audio` 音频分片。

        预期结果：
        1. 启动语音段时会创建 Omni Realtime 会话。
        2. 音频分片会立即推送给 Omni 会话。
        3. 当前播放上下文标记为 `omni_realtime` 音频来源。
        """

        class _OmniSession:
            def __init__(self) -> None:
                self.audio_frames: list[bytes] = []
                self.closed = False

            def append_audio(self, pcm_bytes: bytes) -> None:
                self.audio_frames.append(pcm_bytes)

            def close(self) -> None:
                self.closed = True

        class _OmniClient:
            def __init__(self) -> None:
                self.session = _OmniSession()
                self.started = False

            def start_streaming_reply(self, **_kwargs):
                self.started = True
                return self.session

        omni_client = _OmniClient()
        runtime = VoiceRuntime(
            settings=ServerSettings(voice_reply_mode="omni_realtime"),
            send_control_message=lambda *_args, **_kwargs: None,
            omni_realtime_client=omni_client,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-test")

        runtime.on_segment_started(
            device_id="glass-001",
            session_id="sess-test",
            payload={
                "stream_id": "stream-test",
                "segment_id": "seg-test",
                "sample_rate": 16000,
                "channels": 1,
                "codec": "pcm16",
            },
        )
        runtime.on_audio_frame(
            device_id="glass-001",
            frame=MediaFrame(
                header={
                    "version": 1,
                    "stream_id": "stream-test",
                    "segment_id": "seg-test",
                    "frame_type": "audio_chunk",
                    "seq": 1,
                    "ts_ms": 0,
                    "codec": "pcm16",
                    "payload_size": 2,
                    "final": False,
                },
                payload=b"\x01\x02",
            ),
        )

        self.assertTrue(omni_client.started)
        self.assertEqual(omni_client.session.audio_frames, [b"\x01\x02"])
        controller = runtime._controllers["glass-001"]  # noqa: SLF001 - 单测检查运行时内部状态
        assert controller.current_segment is not None
        self.assertIs(controller.current_segment.omni_realtime_session, omni_client.session)
        self.assertIsNotNone(controller.current_segment.omni_realtime_context)
        self.assertEqual(controller.current_playback.audio_source, "omni_realtime")

    def test_playback_candidate_waits_for_omni_speech_started_before_interrupt(self) -> None:
        """测试目标：验证播放中的候选语音段不会被端侧 VAD 直接打断。

        测试方法：
        1. 先创建一条正在播放的 Omni 回复流。
        2. 播放期间上报新的 `segment.started`，模拟 ESP32 本地 VAD 候选段。
        3. 再手动触发 Omni `speech_started` 回调。

        预期结果：
        1. `segment.started` 本身不会下发 `actuator.audio.interrupt`。
        2. 只有 Omni 确认用户开始说话后，服务端才中断旧播放。
        """

        class _OmniSession:
            def append_audio(self, _pcm_bytes: bytes) -> None:
                return

            def close(self) -> None:
                return

        class _OmniClient:
            def __init__(self) -> None:
                self.on_input_speech_started = None

            def start_streaming_reply(self, **kwargs):
                self.on_input_speech_started = kwargs.get("on_input_speech_started")
                return _OmniSession()

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        omni_client = _OmniClient()
        runtime = VoiceRuntime(
            settings=ServerSettings(voice_reply_mode="omni_realtime"),
            send_control_message=lambda *args: sent_messages.append(args),
            omni_realtime_client=omni_client,
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-test")
        old_context = runtime._open_reply_synthesis_context(  # noqa: SLF001 - 单测需要构造正在播放状态
            device_id="glass-001",
            session_id="sess-test",
            audio_source="omni_realtime",
        )

        runtime.on_segment_started(
            device_id="glass-001",
            session_id="sess-test",
            payload={
                "stream_id": "stream-barge",
                "segment_id": "seg-barge",
                "sample_rate": 16000,
                "channels": 1,
                "codec": "pcm16",
            },
        )

        self.assertEqual([message[2] for message in sent_messages], [])
        controller = runtime._controllers["glass-001"]  # noqa: SLF001 - 单测检查运行时内部状态
        assert controller.current_segment is not None
        self.assertTrue(controller.current_segment.started_during_playback)
        self.assertIsNone(controller.current_segment.omni_realtime_context)

        assert omni_client.on_input_speech_started is not None
        omni_client.on_input_speech_started()

        self.assertTrue(old_context.playback.abort_event.is_set())
        self.assertIn("actuator.audio.interrupt", [message[2] for message in sent_messages])

    def test_playback_candidate_without_omni_speech_started_is_dropped(self) -> None:
        """测试目标：验证播放中误触发候选段不会走 segment_turn 兜底循环。

        测试方法：
        1. 构造一个播放期间启动的候选语音段。
        2. 注入假的 Omni 会话，让 `finish()` 返回 semantic_vad 未自动提交错误。
        3. 调用 Omni 回复分支收尾。

        预期结果：
        1. 运行时直接丢弃候选段。
        2. 不会创建新的下行播放上下文，也不会发送 `assistant.reply`。
        """

        class _OmniSession:
            def finish(self, **_kwargs):
                raise AppError(
                    ErrorCode.TIMEOUT,
                    "Omni semantic_vad 未自动提交",
                    retryable=True,
                    details={"reason": "semantic_vad_no_auto_response"},
                )

            def close(self) -> None:
                return

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(voice_reply_mode="omni_realtime"),
            send_control_message=lambda *args: sent_messages.append(args),
        )
        controller = VoiceSessionController(device_id="glass-001", device_type="glass", session_id="sess-test")
        runtime._controllers["glass-001"] = controller  # noqa: SLF001 - 单测构造运行态
        segment = SegmentBuffer(
            session_id="sess-test",
            stream_id="stream-echo",
            segment_id="seg-echo",
            sample_rate=16000,
            channels=1,
            codec="pcm16",
            started_at_ms=0,
            payload=bytearray(b"\x01\x00" * 1600),
            started_during_playback=True,
            interrupted_playback_stream_id="reply-old",
            omni_realtime_session=_OmniSession(),
        )

        runtime._run_omni_realtime_reply_pipeline(  # noqa: SLF001 - 单测覆盖 Omni 收尾分支
            controller=controller,
            device_id="glass-001",
            session_id="sess-test",
            segment=segment,
            input_path="/tmp/input.wav",
            input_pcm=bytes(segment.payload),
        )

        self.assertEqual(sent_messages, [])
        self.assertIsNone(controller.current_playback)

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

    def test_aborted_synthesis_context_drops_late_audio_chunk(self) -> None:
        """测试目标：验证播放中插话后迟到的旧回复音频不会重新入队。

        测试方法：
        1. 创建一条回复合成上下文。
        2. 模拟用户插话把该播放流标记为中断。
        3. 再向旧上下文注入一段迟到的模型音频。

        预期结果：
        1. 播放队列只保留中断结束哨兵，不会追加新的音频字节。
        2. 运行时不会再次下发旧 stream 的播放请求。
        """

        sent_messages: list[tuple[str, str, str, str, dict]] = []
        runtime = VoiceRuntime(
            settings=ServerSettings(),
            send_control_message=lambda *args: sent_messages.append(args),
        )
        runtime.open_session(device_id="glass-001", device_type="glass", session_id="sess-barge-in")
        runtime.on_voice_session_opened(device_id="glass-001", session_id="sess-barge-in")
        context = runtime._open_reply_synthesis_context(  # noqa: SLF001 - 单测覆盖播放打断后的迟到音频
            device_id="glass-001",
            session_id="sess-barge-in",
        )

        runtime.handle_user_interrupt(
            device_id="glass-001",
            session_id="sess-barge-in",
            reason="voice_barge_in",
            clear_queue=True,
        )
        runtime._emit_synthesis_chunk(  # noqa: SLF001 - 单测覆盖播放打断后的迟到音频
            device_id="glass-001",
            session_id="sess-barge-in",
            context=context,
            chunk=ModelChunk(audio_pcm_bytes=b"\x01\x00" * 160, sample_rate_hz=16000),
        )

        self.assertTrue(context.playback.abort_event.is_set())
        self.assertEqual(context.output_pcm, bytearray())
        self.assertIsNone(context.playback.first_audio_chunk_at_ms)
        self.assertEqual(sent_messages[-1][2], "actuator.audio.interrupt")

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
