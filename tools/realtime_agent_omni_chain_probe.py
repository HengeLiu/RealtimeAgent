#!/usr/bin/env python3
"""realtime-agent Omni 链路探针。

主要功能：
1. 使用当前 `realtime_agent.agent_core.omni.QwenOmniRealtimeAdapter`，不绕过主链路 adapter。
2. 发送用户音频，等待 Omni 触发 `capture_photo` 工具调用。
3. 模拟工具返回本地图片，让当前 adapter 执行工具结果回填、音频重放、图片追加和后续响应。
4. 记录 adapter 收到和发出的关键事件，验证“提交音频 -> 调用工具 -> 提交图片 -> 回复”的真实链路。

示例：
    uv run python tools/realtime_agent_omni_chain_probe.py \
      --audio 'testdata/audio-sample/看一下我前面有什么.wav' \
      --image 'examples/simple-agent-server/runs/user-browser-glass-001/dev-browser-glass-001/photos/asset_5c68b990ae0e.jpg' \
      --out runs/omni-realtime-probe/realtime-agent-chain-events.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import wave
from pathlib import Path
from typing import Any

from realtime_agent.agent_core.omni import QwenOmniRealtimeAdapter, RealtimeProviderCallbacks, RealtimeProviderConfig
from realtime_agent.protocol import StreamChunk


def _json_default(value: Any) -> str:
    """把不能 JSON 序列化的对象转成字符串。"""

    return str(value)


class ChainRecorder:
    """记录当前 realtime-agent adapter 链路事件。"""

    def __init__(self, out_path: Path) -> None:
        self.started_at = time.monotonic()
        self.out_path = out_path
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_text("", encoding="utf-8")
        self.audio_delta_count = 0
        self.audio_delta_bytes = 0
        self.audio_done_count = 0
        self.tool_done_count = 0

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        """写入一条 JSONL 事件并打印摘要。"""

        record = {
            "t_ms": round((time.monotonic() - self.started_at) * 1000, 3),
            "kind": kind,
            **payload,
        }
        with self.out_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
        event = payload.get("event") or kind
        compact = {
            key: payload.get(key)
            for key in ("response_id", "item_id", "tool_call_id", "tool_name", "status", "reason")
            if payload.get(key) not in (None, "")
        }
        print(f"{record['t_ms']:>10.3f}ms {kind:<16} {event} {json.dumps(compact, ensure_ascii=False)}")


def _read_audio_bytes(path: Path) -> tuple[bytes, int]:
    """读取 16k 单声道 PCM16 音频。"""

    if path.suffix.lower() == ".wav":
        with wave.open(str(path), "rb") as wav:
            channels = wav.getnchannels()
            sample_rate = wav.getframerate()
            sample_width = wav.getsampwidth()
            if channels != 1 or sample_rate != 16000 or sample_width != 2:
                raise ValueError(
                    f"WAV 必须是 16kHz/mono/pcm16，当前 channels={channels}, "
                    f"sample_rate={sample_rate}, sample_width={sample_width}"
                )
            return wav.readframes(wav.getnframes()), sample_rate
    return path.read_bytes(), 16000


def _chunk_bytes(payload: bytes, *, sample_rate: int, chunk_ms: int) -> list[bytes]:
    """按固定毫秒切分 PCM16 音频。"""

    bytes_per_ms = sample_rate * 2 // 1000
    size = max(2, bytes_per_ms * chunk_ms)
    return [payload[index : index + size] for index in range(0, len(payload), size)]


def _capture_photo_schema(*, flat: bool) -> dict[str, Any]:
    """构造 `capture_photo` 工具 schema。

    `flat=True` 使用当前 realtime-agent adapter 实际传给 Qwen 的扁平结构；
    `flat=False` 使用官方文档的嵌套结构，用于对照。
    """

    function = {
        "name": "capture_photo",
        "description": "当用户要求查看眼前内容时，调用该工具获取一张实时照片。",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "调用抓拍的原因"},
            },
        },
    }
    if flat:
        return {"type": "function", **function}
    return {"type": "function", "function": function}


def _build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数。"""

    parser = argparse.ArgumentParser(description="测试当前 realtime-agent Qwen Omni adapter 的音频-工具-图片链路。")
    parser.add_argument("--audio", type=Path, required=True, help="16k mono pcm16 的 .wav 或 .pcm 文件")
    parser.add_argument("--image", type=Path, required=True, help="capture_photo 工具返回的本地图片")
    parser.add_argument("--out", type=Path, default=Path("runs/omni-realtime-probe/realtime-agent-chain-events.jsonl"))
    parser.add_argument("--model", default="qwen3.5-omni-plus-realtime")
    parser.add_argument("--voice", default="Tina")
    parser.add_argument("--chunk-ms", type=int, default=20)
    parser.add_argument("--sleep-ms", type=int, default=20)
    parser.add_argument("--vad-tail-seconds", type=float, default=1.5)
    parser.add_argument("--wait-seconds", type=float, default=15.0)
    parser.add_argument("--nested-tool-schema", action="store_true", help="使用官方嵌套工具 schema 做对照")
    parser.add_argument("--send-final", action="store_true", help="发送 final chunk，模拟端侧显式结束输入流")
    return parser


def main() -> int:
    """运行当前 realtime-agent adapter 的真实链路探针。"""

    args = _build_arg_parser().parse_args()
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("缺少 DASHSCOPE_API_KEY，无法连接 DashScope Omni Realtime。", file=sys.stderr)
        return 2
    image_path = args.image.resolve()
    if not image_path.is_file():
        print(f"图片不存在：{image_path}", file=sys.stderr)
        return 2

    recorder = ChainRecorder(args.out)
    audio_bytes, sample_rate = _read_audio_bytes(args.audio)
    original_chunks = _chunk_bytes(audio_bytes, sample_rate=sample_rate, chunk_ms=args.chunk_ms)
    send_chunks = list(original_chunks)
    if args.vad_tail_seconds > 0:
        send_chunks.append(b"\x00" * int(16000 * 2 * args.vad_tail_seconds))

    config = RealtimeProviderConfig(
        provider="qwen",
        model=args.model,
        voice=args.voice,
        turn_detection="provider",
        instructions=(
            "你是中文语音助手。用户询问眼前有什么时，必须调用 capture_photo，"
            "拿到照片后只根据照片内容简短回答。"
        ),
        tools=[_capture_photo_schema(flat=not args.nested_tool_schema)],
    )
    adapter = QwenOmniRealtimeAdapter(config)

    def on_audio_delta(audio: bytes, _format: Any, metadata: dict[str, Any]) -> None:
        recorder.audio_delta_count += 1
        recorder.audio_delta_bytes += len(audio)
        if recorder.audio_delta_count <= 3:
            recorder.write(
                "audio_delta",
                {
                    "event": "assistant_audio.delta",
                    "audio_bytes": len(audio),
                    "provider_event": metadata.get("provider_event"),
                },
            )

    def on_audio_done(metadata: dict[str, Any]) -> None:
        recorder.audio_done_count += 1
        recorder.write("audio_done", {"event": "assistant_audio.done", **metadata})

    def on_provider_event(record: dict[str, Any]) -> None:
        recorder.write("provider_event", record)

    def on_error(message: str, record: dict[str, Any]) -> None:
        recorder.write("error", {"event": record.get("event") or "adapter.error", "message": message, **record})

    def on_tool_delta(record: dict[str, Any]) -> None:
        recorder.write("tool_delta", record)

    def on_tool_done(record: dict[str, Any]) -> dict[str, Any]:
        recorder.tool_done_count += 1
        recorder.write("tool_done", record)
        return {
            "tool_call_id": record.get("tool_call_id"),
            "name": "capture_photo",
            "ok": True,
            "data": {
                "storage_uri": str(image_path),
                "mime_type": "image/jpeg",
            },
            "message": "已完成一次抓拍。",
            "error": None,
            "meta": {"probe": "realtime_agent_omni_chain"},
        }

    def replay_audio_for_tool_result(result: dict[str, Any]) -> list[bytes]:
        recorder.write(
            "replay_audio",
            {
                "event": "omni.input_audio.replay.prepared",
                "tool_call_id": result.get("tool_call_id"),
                "tool_name": result.get("name"),
                "chunk_count": len(original_chunks),
                "payload_size": sum(len(chunk) for chunk in original_chunks),
            },
        )
        return list(original_chunks)

    callbacks = RealtimeProviderCallbacks(
        audio_delta=on_audio_delta,
        audio_done=on_audio_done,
        provider_event=on_provider_event,
        error=on_error,
        tool_call_delta=on_tool_delta,
        tool_call_done=on_tool_done,
        replay_audio_for_tool_result=replay_audio_for_tool_result,
    )

    try:
        recorder.write(
            "client_action",
            {
                "event": "adapter.open",
                "tool_schema": "nested" if args.nested_tool_schema else "flat",
                "send_final": args.send_final,
            },
        )
        adapter.open(user_id="probe-user", session_id="probe-session", callbacks=callbacks)
        stream_id = f"probe_stream_{int(time.time())}"
        for index, chunk in enumerate(send_chunks):
            is_last = index == len(send_chunks) - 1
            adapter.append_audio(
                StreamChunk(
                    user_id="probe-user",
                    session_id="probe-session",
                    stream_id=stream_id,
                    stream_type="sensor.mic",
                    seq=index,
                    payload=chunk,
                    sample_rate=16000,
                    channels=1,
                    duration_ms=args.chunk_ms if chunk else 0,
                    final=bool(args.send_final and is_last),
                    metadata={"probe": "realtime_agent_omni_chain"},
                )
            )
            if index == 0 or is_last:
                recorder.write(
                    "client_action",
                    {
                        "event": "adapter.append_audio",
                        "seq": index,
                        "payload_size": len(chunk),
                        "final": bool(args.send_final and is_last),
                    },
                )
            time.sleep(max(0, args.sleep_ms) / 1000)
        recorder.write("client_action", {"event": "wait", "seconds": args.wait_seconds})
        time.sleep(max(0.0, args.wait_seconds))
        recorder.write(
            "summary",
            {
                "event": "probe.summary",
                "tool_done_count": recorder.tool_done_count,
                "audio_delta_count": recorder.audio_delta_count,
                "audio_delta_bytes": recorder.audio_delta_bytes,
                "audio_done_count": recorder.audio_done_count,
            },
        )
    finally:
        recorder.write("client_action", {"event": "adapter.close"})
        adapter.close(user_id="probe-user", reason="probe_finished")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
