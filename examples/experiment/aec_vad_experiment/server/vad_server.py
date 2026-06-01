#!/usr/bin/env python3
"""轻量独立 VAD 实验服务。

测试目标：接收 iOS 实验 App 上传的 WAV，判断是否存在明显语音段。
测试方法：优先使用可选依赖 webrtcvad；未安装时使用能量阈值兜底，避免实验服务引入重依赖。
预期结果：返回 JSON，包含是否触发、语音帧数量、首个语音帧时间和简单统计。
"""

from __future__ import annotations

import argparse
import audioop
import json
import statistics
import wave
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from typing import Any


@dataclass(frozen=True)
class VADResult:
    """VAD 分析结果。"""

    triggered: bool
    speech_frames: int
    total_frames: int
    first_speech_ms: int | None
    speech_ratio: float
    rms_floor: float
    rms_threshold: float
    backend: str


def analyze_wav(data: bytes, aggressive: int) -> VADResult:
    """分析 WAV 字节并返回 VAD 结果。

    主要逻辑：读取 16-bit PCM WAV，切成 20ms 帧；如果本地安装了 webrtcvad 就直接使用，
    否则用前几帧估计噪声底，再以 RMS 阈值判断明显语音段。
    参数：`data` 是 WAV 字节；`aggressive` 是 webrtcvad 激进度，取值 0-3。
    返回值：结构化 VAD 结果。
    异常情况：非 WAV 或非 16-bit PCM 时抛出 ValueError。
    """

    with wave.open(BytesIO(data), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if sample_width != 2:
        raise ValueError(f"只支持 16-bit PCM WAV，当前 sample_width={sample_width}")
    if channels != 1:
        frames = audioop.tomono(frames, sample_width, 0.5, 0.5)

    frame_ms = 20
    samples_per_frame = max(1, int(sample_rate * frame_ms / 1000))
    bytes_per_frame = samples_per_frame * sample_width
    chunks = [
        frames[offset : offset + bytes_per_frame]
        for offset in range(0, len(frames) - bytes_per_frame + 1, bytes_per_frame)
    ]
    if not chunks:
        return VADResult(False, 0, 0, None, 0.0, 0.0, 0.0, "empty")

    try:
        import webrtcvad  # type: ignore

        vad = webrtcvad.Vad(max(0, min(3, aggressive)))
        speech_flags = [vad.is_speech(chunk, sample_rate) for chunk in chunks]
        backend = "webrtcvad"
        rms_values = [float(audioop.rms(chunk, sample_width)) for chunk in chunks]
        floor = statistics.median(rms_values[: min(10, len(rms_values))])
        threshold = 0.0
    except Exception:
        rms_values = [float(audioop.rms(chunk, sample_width)) for chunk in chunks]
        floor = statistics.median(rms_values[: min(15, len(rms_values))])
        spread = statistics.pstdev(rms_values[: min(30, len(rms_values))]) if len(rms_values) > 1 else 0.0
        threshold = max(350.0, floor * 3.0, floor + spread * 4.0)
        speech_flags = [rms > threshold for rms in rms_values]
        backend = "rms"

    speech_frames = sum(1 for flag in speech_flags if flag)
    first = next((index for index, flag in enumerate(speech_flags) if flag), None)
    speech_ratio = speech_frames / max(1, len(speech_flags))
    triggered = speech_frames >= 3 and speech_ratio >= 0.03
    return VADResult(
        triggered=triggered,
        speech_frames=speech_frames,
        total_frames=len(speech_flags),
        first_speech_ms=None if first is None else first * frame_ms,
        speech_ratio=speech_ratio,
        rms_floor=floor,
        rms_threshold=threshold,
        backend=backend,
    )


class VADHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器。"""

    aggressive = 2

    def do_GET(self) -> None:
        """返回健康检查。"""

        if self.path != "/health":
            self.send_error(404)
            return
        self._send_json({"ok": True})

    def do_POST(self) -> None:
        """处理 `/vad/analyze` WAV 上传请求。"""

        if not self.path.startswith("/vad/analyze"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        data = self.rfile.read(length)
        try:
            result = analyze_wav(data, self.aggressive)
        except Exception as exc:  # noqa: BLE001 - 实验服务需要把错误直接反馈给手机端。
            self._send_json({"ok": False, "error": str(exc)}, status=400)
            return
        payload: dict[str, Any] = {"ok": True, **result.__dict__}
        self._send_json(payload)

    def log_message(self, format: str, *args: Any) -> None:
        """打印简洁请求日志。"""

        print(f"{self.address_string()} {format % args}")

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> None:
    """启动 VAD 实验服务。"""

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8777)
    parser.add_argument("--aggressive", type=int, default=2)
    args = parser.parse_args()
    VADHandler.aggressive = args.aggressive
    server = ThreadingHTTPServer((args.host, args.port), VADHandler)
    print(f"VAD server listening on http://{args.host}:{args.port}/vad/analyze")
    server.serve_forever()


if __name__ == "__main__":
    main()
