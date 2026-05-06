"""语音运行时音频工具。"""

from __future__ import annotations

import io
import math
import struct
import wave

from runtime.voice_constants import SERVER_SAMPLE_WIDTH_BYTES


class PCM16StreamResampler:
    """流式 PCM16 单声道重采样器。

    主要功能：
    1. 接收连续 PCM16 单声道音频分片。
    2. 使用线性插值把输入采样率转换为输出采样率。
    3. 保存跨分片的边界样本，避免每个分片单独重采样造成音频断裂。
    """

    def __init__(self, input_rate_hz: int, output_rate_hz: int) -> None:
        """初始化重采样器。

        参数：
        1. `input_rate_hz`：输入 PCM 采样率。
        2. `output_rate_hz`：输出 PCM 采样率。

        返回值：无。
        异常情况：本方法不主动抛出业务异常；非法采样率会在后续计算中暴露。
        """

        self._input_rate_hz = input_rate_hz
        self._output_rate_hz = output_rate_hz
        self._position = 0.0
        self._carry: list[int] = []

    def push(self, pcm_bytes: bytes, *, final: bool = False) -> bytes:
        """追加一段 PCM 并返回已重采样的输出。

        主要逻辑：
        1. 输入输出采样率相同时直接返回原始字节。
        2. 采样率不同时，把当前分片和上轮遗留样本拼接后做线性插值。
        3. 非最后分片会保留末尾样本，供下一分片连续计算。

        参数：
        1. `pcm_bytes`：PCM16 little-endian 单声道字节。
        2. `final`：是否为最后一个分片。

        返回值：
        1. 输出采样率下的 PCM16 little-endian 单声道字节。

        异常情况：
        1. 输入长度不是 2 的倍数时，最后一个不完整字节会被忽略。
        """

        if self._input_rate_hz == self._output_rate_hz:
            return pcm_bytes

        sample_count = len(pcm_bytes) // 2
        if sample_count == 0:
            if final:
                self._carry.clear()
                self._position = 0.0
            return b""

        samples = list(self._carry)
        samples.extend(struct.unpack("<" + "h" * sample_count, pcm_bytes[: sample_count * 2]))
        if len(samples) < 2 and not final:
            self._carry = samples
            return b""

        step = self._input_rate_hz / self._output_rate_hz
        max_position = len(samples) - 1 if final else len(samples) - 2
        out_samples: list[int] = []
        while self._position <= max_position:
            index = int(self._position)
            frac = self._position - index
            left = samples[index]
            right = samples[index + 1] if index + 1 < len(samples) else left
            value = int(round(left + (right - left) * frac))
            value = max(-32768, min(32767, value))
            out_samples.append(value)
            self._position += step

        keep_from = max(0, int(math.floor(self._position)) - 1)
        self._carry = samples[keep_from:]
        self._position -= keep_from

        if final and out_samples:
            self._carry.clear()
            self._position = 0.0

        return struct.pack("<" + "h" * len(out_samples), *out_samples) if out_samples else b""


def build_wav_bytes(pcm_bytes: bytes, sample_rate_hz: int, channels: int = 1) -> bytes:
    """把 PCM16 数据封装为 WAV。

    参数：
    1. `pcm_bytes`：PCM16 little-endian 音频字节。
    2. `sample_rate_hz`：采样率。
    3. `channels`：声道数。

    返回值：
    1. 完整 WAV 文件字节。

    异常情况：
    1. 底层 `wave` 写入失败时会抛出标准库异常。
    """

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(SERVER_SAMPLE_WIDTH_BYTES)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def wav_header_unknown_size(sample_rate_hz: int, channels: int, sample_width_bytes: int = 2) -> bytes:
    """生成适用于 chunked 流的 WAV 头。

    主要逻辑：
    1. 由于 HTTP chunked 播放开始时还不知道总长度，使用一个足够大的 data size。
    2. 眼镜端按流式数据读取 PCM，不依赖最终 RIFF 长度精确值。

    参数：
    1. `sample_rate_hz`：采样率。
    2. `channels`：声道数。
    3. `sample_width_bytes`：单样本字节数。

    返回值：
    1. WAV 文件头字节。

    异常情况：
    1. 参数非法时不会主动抛业务异常，但生成的头可能无法被播放器识别。
    """

    byte_rate = sample_rate_hz * channels * sample_width_bytes
    block_align = channels * sample_width_bytes
    data_size = 0x7FFFFFF0
    riff_size = 36 + data_size
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        channels,
        sample_rate_hz,
        byte_rate,
        block_align,
        sample_width_bytes * 8,
        b"data",
        data_size,
    )
