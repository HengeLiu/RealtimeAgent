from __future__ import annotations

import json
import os
import time
import wave
from pathlib import Path
from typing import Any


PROVIDER_RUNS_DIR = Path(os.getenv("REALTIME_AGENT_PROVIDER_TEST_RUNS", "runs/provider-tests/latest"))


def elapsed_ms(started: float) -> int:
    """返回从 `started` 到当前时间的毫秒数。"""

    return int((time.monotonic() - started) * 1000)


def write_provider_result(name: str, payload: dict[str, Any]) -> Path:
    """把真实 provider 测试结果写入固定 runs 目录。

    主要逻辑：所有 L2 artifact 都落在 `runs/provider-tests/latest/`，避免混入源码
    fixture；JSON 中补充写入时间，便于和分层回归报告互相定位。
    参数：`name` 为文件名，`payload` 为可 JSON 序列化的结果。
    返回值：写出的文件路径。
    异常情况：文件系统异常直接抛出，让测试暴露真实产物写入问题。
    """

    PROVIDER_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROVIDER_RUNS_DIR / name
    data = {"written_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), **payload}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def write_wav(name: str, pcm: bytes, *, sample_rate_hz: int) -> Path:
    """把 provider 输出的 16-bit mono PCM 包装成 WAV 证据文件。

    主要逻辑：测试中收集到的是 provider 原始 PCM；WAV 包装只用于人工回放和归档，
    不改变断言输入。
    参数：`name` 为输出文件名，`pcm` 为音频字节，`sample_rate_hz` 为采样率。
    返回值：写出的 WAV 路径。
    异常情况：文件系统异常直接抛出。
    """

    PROVIDER_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = PROVIDER_RUNS_DIR / name
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate_hz)
        handle.writeframes(pcm)
    return path
