#!/usr/bin/env python3
"""把 audio-sample 目录下的 m4a 批量转换成 Phase C 可用 wav。"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import struct
import tempfile
import wave
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    返回值：
    1. `argparse.Namespace`：包含输入目录和输出目录。
    """

    current_dir = Path(__file__).resolve().parent / "m4a"
    parser = argparse.ArgumentParser(description="批量转换 audio-sample 目录下的 m4a")
    parser.add_argument(
        "--input-dir",
        default=str(current_dir),
        help="待转换音频目录，默认当前 audio-sample 目录",
    )
    parser.add_argument(
        "--output-dir",
        default=str(current_dir / "wav"),
        help="转换后的 wav 输出目录",
    )
    parser.add_argument(
        "--normalize-existing-wav",
        action="store_true",
        help="不读取 m4a，直接把 output-dir 中已有 wav 原地规范化为 16kHz/mono/16bit 标准 PCM WAV",
    )
    return parser.parse_args()


def main() -> None:
    """脚本主入口。

    主要逻辑：
    1. 扫描输入目录中的 m4a 文件。
    2. 逐个转成 16kHz、单声道、16bit 的标准 PCM WAV。
    3. 每个文件转换后做格式校验，避免生成 `WAVE_FORMAT_EXTENSIBLE` 等回放端不稳定格式。
    """

    args = parse_args()
    input_dir = Path(args.input_dir).resolve()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    repo_root = Path(__file__).resolve().parents[4]

    if args.normalize_existing_wav:
        wav_files = sorted(output_dir.glob("*.wav"))
        if not wav_files:
            print(f"no wav files found in {output_dir}")
            return
        for wav_path in wav_files:
            normalize_existing_wav(wav_path)
            print(f"normalized: {wav_path}")
        return

    sources = sorted(input_dir.glob("*.m4a"))
    if not sources:
        print(f"no m4a files found in {input_dir}")
        return

    for source in sources:
        target = output_dir / f"{source.stem}.wav"
        convert_m4a_to_wav(repo_root, source, target)
        print(f"converted: {source.name} -> {target}")


def convert_m4a_to_wav(repo_root: Path, source: Path, target: Path) -> None:
    """把单个 m4a 转成 16kHz/mono/16bit 标准 PCM WAV。

    主要逻辑：
    1. 优先使用 ffmpeg，因为它能稳定写出普通 `WAVE_FORMAT_PCM`。
    2. 没有 ffmpeg 时使用 macOS `afconvert`，并在输出后重写 WAV 头。
    3. 最后校验输出格式，避免把 48kHz 或 `WAVE_FORMAT_EXTENSIBLE` 样例混入测试数据。

    参数：
    1. `repo_root`：仓库根目录，当前保留用于后续扩展。
    2. `source`：源 m4a 文件。
    3. `target`：目标 wav 文件。

    异常情况：
    1. 本机没有 ffmpeg 或 afconvert 时抛出 `RuntimeError`。
    2. 转换产物不是标准 16kHz/mono/16bit PCM WAV 时抛出 `ValueError`。
    """

    del repo_root
    target.parent.mkdir(parents=True, exist_ok=True)

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-i",
                str(source),
                "-ar",
                "16000",
                "-ac",
                "1",
                "-sample_fmt",
                "s16",
                "-acodec",
                "pcm_s16le",
                str(target),
            ],
            check=True,
        )
        validate_standard_pcm16_wav(target)
        return

    afconvert = shutil.which("afconvert")
    if afconvert:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_target = Path(temp_dir) / target.name
            subprocess.run(
                [
                    afconvert,
                    "-f",
                    "WAVE",
                    "-d",
                    "LEI16@16000",
                    "-c",
                    "1",
                    str(source),
                    str(temp_target),
                ],
                check=True,
            )
            rewrite_as_standard_pcm16_wav(temp_target, target)
        validate_standard_pcm16_wav(target)
        return

    raise RuntimeError("未找到可用转码器：需要 ffmpeg 或 macOS 的 afconvert")


def rewrite_as_standard_pcm16_wav(source: Path, target: Path) -> None:
    """把 16bit PCM WAV 重写成标准 `WAVE_FORMAT_PCM` 头。

    主要逻辑：
    1. 读取普通 PCM 或 `WAVE_FORMAT_EXTENSIBLE` 的 PCM 子格式。
    2. 使用 Python `wave` 模块重新写出普通 PCM WAV。

    参数：
    1. `source`：源 WAV 文件。
    2. `target`：目标 WAV 文件。

    异常情况：
    1. 源文件不是 16bit PCM WAV 时抛出 `ValueError`。
    """

    pcm_bytes, sample_rate_hz, channels = read_pcm16_wav(source)
    with wave.open(str(target), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate_hz)
        wav_file.writeframes(pcm_bytes)


def read_pcm16_wav(path: Path) -> tuple[bytes, int, int]:
    """读取普通 PCM 或扩展 PCM WAV。

    参数：
    1. `path`：WAV 文件路径。

    返回值：
    1. `(pcm_bytes, sample_rate_hz, channels)`。

    异常情况：
    1. 文件不是 RIFF/WAVE、缺少 `fmt/data` 或不是 16bit PCM 时抛出 `ValueError`。
    """

    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise ValueError(f"不是有效 WAV 文件: {path}")

    fmt_chunk: bytes | None = None
    data_chunks: list[bytes] = []
    offset = 12
    while offset + 8 <= len(raw):
        chunk_id = raw[offset : offset + 4]
        chunk_size = struct.unpack_from("<I", raw, offset + 4)[0]
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        if payload_end > len(raw):
            raise ValueError(f"WAV chunk 长度异常: {path}")
        payload = raw[payload_start:payload_end]
        if chunk_id == b"fmt ":
            fmt_chunk = payload
        elif chunk_id == b"data":
            data_chunks.append(payload)
        offset = payload_end + (chunk_size % 2)

    if fmt_chunk is None or not data_chunks:
        raise ValueError(f"WAV 缺少 fmt 或 data chunk: {path}")
    if len(fmt_chunk) < 16:
        raise ValueError(f"WAV fmt chunk 过短: {path}")

    audio_format, channels, sample_rate_hz, _byte_rate, _block_align, bits_per_sample = struct.unpack_from(
        "<HHIIHH",
        fmt_chunk,
        0,
    )
    if audio_format == 0xFFFE:
        if len(fmt_chunk) < 40:
            raise ValueError(f"WAVE_FORMAT_EXTENSIBLE fmt chunk 过短: {path}")
        subformat = fmt_chunk[24:40]
        pcm_subformat = b"\x01\x00\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        if subformat != pcm_subformat:
            raise ValueError(f"WAVE_FORMAT_EXTENSIBLE 仅支持 PCM 子格式: {path}")
        audio_format = 1
    if audio_format != 1 or bits_per_sample != 16:
        raise ValueError(f"仅支持 16bit PCM WAV: {path}")
    return b"".join(data_chunks), sample_rate_hz, channels


def validate_standard_pcm16_wav(path: Path) -> None:
    """校验转换产物是标准 16kHz/mono/16bit PCM WAV。

    参数：
    1. `path`：待校验的 WAV 文件。

    异常情况：
    1. 格式码、采样率、声道数或位宽不符合要求时抛出 `ValueError`。
    """

    with wave.open(str(path), "rb") as wav_file:
        params = wav_file.getparams()
    if params.nchannels != 1 or params.sampwidth != 2 or params.framerate != 16000:
        raise ValueError(f"转换结果不是 16kHz/mono/16bit WAV: {path} params={params}")

    raw = path.read_bytes()
    fmt_index = raw.find(b"fmt ")
    if fmt_index < 0 or len(raw) < fmt_index + 18:
        raise ValueError(f"转换结果缺少 fmt chunk: {path}")
    audio_format = struct.unpack_from("<H", raw, fmt_index + 8)[0]
    if audio_format != 1:
        raise ValueError(f"转换结果不是标准 PCM WAV: {path} format={audio_format}")


def normalize_existing_wav(path: Path) -> None:
    """把已有 WAV 文件原地规范化为标准 16kHz/mono/16bit PCM WAV。

    主要用途：
    1. 修复历史脚本已经生成的 `WAVE_FORMAT_EXTENSIBLE` 测试样例。
    2. 保持脚本只依赖本地 ffmpeg 或 afconvert。
    """

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_target = Path(temp_dir) / path.name
            subprocess.run(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(path),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-sample_fmt",
                    "s16",
                    "-acodec",
                    "pcm_s16le",
                    str(temp_target),
                ],
                check=True,
            )
            shutil.move(str(temp_target), str(path))
        validate_standard_pcm16_wav(path)
        return

    afconvert = shutil.which("afconvert")
    if not afconvert:
        raise RuntimeError("未找到可用转码器：需要 ffmpeg 或 macOS 的 afconvert")

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_source = Path(temp_dir) / f"source-{path.name}"
        temp_target = Path(temp_dir) / path.name
        rewrite_as_standard_pcm16_wav(path, temp_source)
        subprocess.run(
            [
                afconvert,
                "-f",
                "WAVE",
                "-d",
                "LEI16@16000",
                "-c",
                "1",
                str(temp_source),
                str(temp_target),
            ],
            check=True,
        )
        rewrite_as_standard_pcm16_wav(temp_target, path)
    validate_standard_pcm16_wav(path)


if __name__ == "__main__":
    main()
