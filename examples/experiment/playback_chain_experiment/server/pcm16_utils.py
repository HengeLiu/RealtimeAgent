"""PCM16 音频处理工具。

主要功能：提供实验服务需要的单声道转换、简单重采样和 RMS 计算。
主要方法：输入和输出都使用 little-endian PCM16 bytes，避免依赖 Python 3.13 已移除的 audioop。
"""

from __future__ import annotations

import math


def pcm16_to_samples(data: bytes) -> list[int]:
    """把 little-endian PCM16 字节转换成整数样本。

    主要逻辑：每两个字节按有符号 16 位整数解析。
    参数：`data` 是 PCM16 little-endian 字节。
    返回值：整数样本列表。
    异常情况：奇数字节会忽略最后 1 字节。
    """

    return [int.from_bytes(data[index : index + 2], "little", signed=True) for index in range(0, len(data) - 1, 2)]


def samples_to_pcm16(samples: list[int]) -> bytes:
    """把整数样本转换成 little-endian PCM16 字节。

    主要逻辑：对样本做 16 位范围裁剪，再写入字节流。
    参数：`samples` 是整数样本列表。
    返回值：PCM16 little-endian 字节。
    异常情况：无。
    """

    output = bytearray()
    for sample in samples:
        clipped = max(-32768, min(32767, int(round(sample))))
        output.extend(clipped.to_bytes(2, "little", signed=True))
    return bytes(output)


def pcm16_to_mono(data: bytes, channels: int) -> bytes:
    """把多声道 PCM16 转成单声道。

    主要逻辑：按帧读取多个声道并取平均值。
    参数：`data` 是 PCM16 字节；`channels` 是声道数。
    返回值：单声道 PCM16 字节。
    异常情况：声道数小于等于 1 时直接返回原始数据。
    """

    if channels <= 1:
        return data
    samples = pcm16_to_samples(data)
    mono: list[int] = []
    for offset in range(0, len(samples) - channels + 1, channels):
        frame = samples[offset : offset + channels]
        mono.append(int(sum(frame) / channels))
    return samples_to_pcm16(mono)


def resample_pcm16_mono(data: bytes, source_rate: int, target_rate: int) -> bytes:
    """对单声道 PCM16 做线性重采样。

    主要逻辑：按目标采样点映射到源采样位置，并对相邻样本做线性插值。
    参数：`data` 是单声道 PCM16；`source_rate` 和 `target_rate` 是采样率。
    返回值：重采样后的单声道 PCM16。
    异常情况：采样率无效或源样本为空时返回原始数据。
    """

    if source_rate == target_rate or source_rate <= 0 or target_rate <= 0:
        return data
    source = pcm16_to_samples(data)
    if not source:
        return data
    target_count = max(1, int(round(len(source) * target_rate / source_rate)))
    ratio = source_rate / target_rate
    target: list[int] = []
    for index in range(target_count):
        source_pos = index * ratio
        left = int(math.floor(source_pos))
        right = min(left + 1, len(source) - 1)
        frac = source_pos - left
        value = source[left] * (1.0 - frac) + source[right] * frac
        target.append(int(round(value)))
    return samples_to_pcm16(target)


def pcm16_rms(data: bytes) -> float:
    """计算 PCM16 字节的 RMS 能量。

    主要逻辑：平方求均值再开方。
    参数：`data` 是 PCM16 字节。
    返回值：RMS 浮点值。
    异常情况：空数据返回 0。
    """

    samples = pcm16_to_samples(data)
    if not samples:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))
