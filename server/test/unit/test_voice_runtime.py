"""VoiceRuntime 关键工具测试。"""

from __future__ import annotations

import base64
import struct
import unittest

from infra.config import ServerSettings
from runtime.voice_runtime import MessageEntry, PCM16StreamResampler, VoiceRuntime, VoiceSessionController, build_audio_data_url, extract_message_text, wav_header_unknown_size


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


if __name__ == "__main__":
    unittest.main()
