#!/usr/bin/env python3
"""Omni Realtime 事件探针。

主要功能：
1. 直接连接 DashScope Omni Realtime，不经过 realtime-agent 主链路。
2. 发送一段 PCM/WAV 音频，可选发送图片和测试工具定义。
3. 把服务端原始事件、关键 ID 和事件时机写入 JSONL，便于核对 turn 生命周期。

使用前提：
- 已安装本仓库依赖：`uv sync --python 3.11`
- 已设置 `DASHSCOPE_API_KEY`

示例：
    uv run python tools/omni_realtime_event_probe.py \
      --audio testdata/audio/look_front.wav \
      --image /tmp/current.jpg \
      --with-tool \
      --out runs/omni-probe/events.jsonl
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import threading
import time
import wave
from pathlib import Path
from typing import Any


def _json_default(value: Any) -> str:
    """把 SDK 中不可 JSON 化的对象转成字符串。"""

    return str(value)


class EventRecorder:
    """记录 Omni Realtime 原始事件和派生摘要。

    主要属性：
    - `started_at`：脚本启动时间，用于计算相对毫秒。
    - `out_path`：JSONL 输出路径。
    - `function_outputs_sent`：避免同一个工具调用重复回填。
    """

    def __init__(self, out_path: Path, *, auto_tool_result: bool) -> None:
        self.started_at = time.monotonic()
        self.out_path = out_path
        self.auto_tool_result = auto_tool_result
        self.function_outputs_sent: set[str] = set()
        self.conversation: Any | None = None
        self.session_updated = threading.Event()
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self.out_path.write_text("", encoding="utf-8")

    def bind_conversation(self, conversation: Any) -> None:
        """绑定 SDK conversation，供工具调用回填使用。"""

        self.conversation = conversation

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        """写一条 JSONL 事件。

        参数：
        - `kind`：事件来源，例如 `server_event`、`client_action`。
        - `payload`：原始或派生事件内容。
        """

        record = {
            "t_ms": round((time.monotonic() - self.started_at) * 1000, 3),
            "kind": kind,
            **payload,
        }
        with self.out_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
        if payload.get("type") == "session.updated":
            self.session_updated.set()
        event_type = payload.get("type") or payload.get("event") or kind
        ids = _extract_event_ids(payload)
        print(f"{record['t_ms']:>10.3f}ms {kind:<14} {event_type} {ids}")

    def maybe_send_tool_output(self, message: dict[str, Any]) -> None:
        """收到 function call 完成事件后，按官方方式回填 function_call_output。

        主要逻辑：同时兼容 `response.function_call_arguments.done` 和
        `response.output_item.done`；同一个 `call_id` 只回填一次。
        """

        if not self.auto_tool_result or self.conversation is None:
            return
        event_type = str(message.get("type") or "")
        item = message.get("item") if isinstance(message.get("item"), dict) else {}
        is_arguments_done = event_type in {"response.function_call_arguments.done", "response.tool_call.done"}
        is_item_done = event_type == "response.output_item.done" and item.get("type") in {"function_call", "tool_call"}
        if not is_arguments_done and not is_item_done:
            return
        call_id = str(message.get("call_id") or item.get("call_id") or item.get("id") or "").strip()
        tool_name = str(message.get("name") or item.get("name") or "").strip()
        arguments = message.get("arguments") or item.get("arguments") or ""
        if not call_id or call_id in self.function_outputs_sent:
            return
        self.function_outputs_sent.add(call_id)
        output = {
            "ok": True,
            "tool_name": tool_name,
            "arguments": arguments,
            "probe_note": "这是探针脚本自动回填的工具结果。",
        }
        self.write("client_action", {"event": "conversation.item.create", "call_id": call_id, "tool_name": tool_name})
        self.conversation.create_item(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(output, ensure_ascii=False),
            }
        )
        self.write("client_action", {"event": "response.create", "reason": "after_function_call_output"})
        self.conversation.create_response()


def _extract_event_ids(message: dict[str, Any]) -> str:
    """提取便于人工阅读的事件关联 ID。"""

    item = message.get("item") if isinstance(message.get("item"), dict) else {}
    response = message.get("response") if isinstance(message.get("response"), dict) else {}
    values = {
        "event_id": message.get("event_id"),
        "response_id": message.get("response_id") or response.get("id"),
        "item_id": message.get("item_id") or item.get("id"),
        "call_id": message.get("call_id") or item.get("call_id"),
        "item_type": item.get("type"),
        "status": response.get("status"),
    }
    compact = {key: value for key, value in values.items() if value not in (None, "")}
    return json.dumps(compact, ensure_ascii=False)


def _read_audio_bytes(path: Path) -> tuple[bytes, int]:
    """读取 PCM/WAV 音频并返回 16k 单声道 PCM16。

    异常情况：
    - WAV 不是 16k/单声道/16bit 时直接报错，避免探针引入重采样误差。
    - `.pcm` 默认认为已经是 16k 单声道 PCM16。
    """

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


def _tool_schema(*, nested: bool) -> list[dict[str, Any]]:
    """返回测试工具 schema。

    `nested=True` 使用官方文档结构；`nested=False` 使用当前 realtime-agent 主链路里的扁平结构，
    用来验证 SDK/provider 是否只是做了非文档兼容。
    """

    function = {
        "name": "probe_capture_photo",
        "description": "测试工具：当用户要求查看眼前内容时调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "调用工具的原因"},
            },
        },
    }
    if nested:
        return [{"type": "function", "function": function}]
    return [{"type": "function", **function}]


def _build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数。"""

    parser = argparse.ArgumentParser(description="直接观察 DashScope Omni Realtime 原始事件时序。")
    parser.add_argument("--audio", type=Path, required=True, help="16k mono pcm16 的 .wav 或 .pcm 文件")
    parser.add_argument("--image", type=Path, help="可选 JPEG/PNG 图片")
    parser.add_argument(
        "--image-position",
        choices=["after_first_audio", "before_tail", "after_all_audio"],
        default="after_first_audio",
        help="图片发送时机；VAD 联合输入默认在第一片音频后立即发送",
    )
    parser.add_argument(
        "--repeat-image-seconds",
        type=float,
        default=0.0,
        help="大于 0 时，从第一片音频后开始按该间隔重复提交同一张图片，直到音频和静音尾巴发送结束",
    )
    parser.add_argument("--out", type=Path, default=Path("runs/omni-realtime-probe/events.jsonl"), help="JSONL 输出路径")
    parser.add_argument("--model", default="qwen3.5-omni-plus-realtime", help="Omni Realtime 模型名")
    parser.add_argument("--voice", default="Tina", help="输出音色")
    parser.add_argument("--chunk-ms", type=int, default=20, help="音频分片毫秒")
    parser.add_argument("--sleep-ms", type=int, default=20, help="发送分片之间的等待毫秒")
    parser.add_argument("--wait-seconds", type=float, default=20.0, help="发送后等待服务端事件的秒数")
    parser.add_argument("--instructions", default="你是中文语音助手。请简短回答。", help="session instructions")
    parser.add_argument("--turn-detection", choices=["semantic_vad", "server_vad", "manual"], default="semantic_vad")
    parser.add_argument("--vad-tail-seconds", type=float, default=1.0, help="VAD 模式下发送完音频后补的静音秒数")
    parser.add_argument("--with-tool", action="store_true", help="注册测试工具并自动回填 function_call_output")
    parser.add_argument("--flat-tool-schema", action="store_true", help="使用当前主链路的扁平工具 schema 做对照")
    return parser


def main() -> int:
    """运行 Omni Realtime 探针。"""

    args = _build_arg_parser().parse_args()
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("缺少 DASHSCOPE_API_KEY，无法连接 DashScope Omni Realtime。", file=sys.stderr)
        return 2

    try:
        import dashscope
        from dashscope.audio.qwen_omni import AudioFormat, MultiModality, OmniRealtimeCallback, OmniRealtimeConversation
    except ImportError as exc:
        print(f"缺少 dashscope SDK：{exc}", file=sys.stderr)
        return 2

    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    recorder = EventRecorder(args.out, auto_tool_result=args.with_tool)

    class ProbeCallback(OmniRealtimeCallback):
        """DashScope SDK 回调，逐条记录服务端事件。"""

        def on_open(self) -> None:
            recorder.write("socket", {"event": "opened"})

        def on_close(self, close_status_code: Any, close_msg: Any) -> None:
            recorder.write("socket", {"event": "closed", "code": close_status_code, "message": str(close_msg)})

        def on_event(self, message: dict[str, Any]) -> None:
            recorder.write("server_event", message)
            recorder.maybe_send_tool_output(message)

    conversation = OmniRealtimeConversation(
        model=args.model,
        callback=ProbeCallback(),
        url="wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    )
    recorder.bind_conversation(conversation)
    audio_bytes, sample_rate = _read_audio_bytes(args.audio)
    chunks = _chunk_bytes(audio_bytes, sample_rate=sample_rate, chunk_ms=args.chunk_ms)
    try:
        recorder.write("client_action", {"event": "connect"})
        conversation.connect()
        enable_turn_detection = args.turn_detection != "manual"
        update_kwargs: dict[str, Any] = {
            "output_modalities": [MultiModality.TEXT, MultiModality.AUDIO],
            "voice": args.voice,
            "input_audio_format": AudioFormat.PCM_16000HZ_MONO_16BIT,
            "output_audio_format": AudioFormat.PCM_24000HZ_MONO_16BIT,
            "enable_input_audio_transcription": True,
            "input_audio_transcription_model": "paraformer-realtime-v2",
            "enable_turn_detection": enable_turn_detection,
            "instructions": args.instructions,
        }
        if enable_turn_detection:
            update_kwargs["turn_detection_type"] = args.turn_detection
        if args.with_tool:
            update_kwargs["tools"] = _tool_schema(nested=not args.flat_tool_schema)
        recorder.write(
            "client_action",
            {
                "event": "session.update",
                "turn_detection": args.turn_detection,
                "tool_schema": "flat" if args.flat_tool_schema else "nested",
                "tool_count": len(update_kwargs.get("tools") or []),
            },
        )
        conversation.update_session(**update_kwargs)
        recorder.write("client_action", {"event": "wait_session_updated"})
        recorder.session_updated.wait(timeout=5)

        send_chunks = list(chunks)
        if enable_turn_detection and args.vad_tail_seconds > 0:
            silence_bytes = int(16000 * 2 * args.vad_tail_seconds)
            send_chunks.append(b"\x00" * silence_bytes)
        image_sent = False
        next_image_at = 0.0
        first_audio_at: float | None = None

        def append_image(position: str, *, repeated: bool = False) -> None:
            """发送图片输入。

            参数：
            - `position`：图片相对音频发送过程的位置说明。
            - `repeated`：是否属于周期性重复提交。
            """

            nonlocal image_sent
            if not repeated and image_sent:
                return
            if not args.image:
                return
            image_bytes = args.image.read_bytes()
            recorder.write(
                "client_action",
                {
                    "event": "input_image_buffer.append",
                    "position": position,
                    "bytes": len(image_bytes),
                    "repeated": repeated,
                },
            )
            conversation.append_video(base64.b64encode(image_bytes).decode("ascii"))
            image_sent = True

        for index, chunk in enumerate(send_chunks):
            now = time.monotonic()
            if first_audio_at is None:
                first_audio_at = now
            conversation.append_audio(base64.b64encode(chunk).decode("ascii"))
            if index == 0 or index == len(send_chunks) - 1:
                recorder.write("client_action", {"event": "input_audio_buffer.append", "seq": index, "bytes": len(chunk)})
            if args.repeat_image_seconds > 0 and args.image:
                elapsed = now - first_audio_at
                if index == 0 or elapsed >= next_image_at:
                    append_image(f"repeat_{elapsed:.2f}s", repeated=True)
                    next_image_at = elapsed + args.repeat_image_seconds
            elif index == 0 and args.image_position == "after_first_audio":
                append_image("after_first_audio")
            elif args.image_position == "before_tail" and args.vad_tail_seconds > 0 and index == len(chunks) - 1:
                append_image("before_tail")
            time.sleep(max(0, args.sleep_ms) / 1000)
        if args.repeat_image_seconds <= 0 and args.image_position == "after_all_audio":
            append_image("after_all_audio")
        if args.turn_detection == "manual":
            recorder.write("client_action", {"event": "input_audio_buffer.commit"})
            conversation.commit()
            recorder.write("client_action", {"event": "response.create", "reason": "manual_turn"})
            conversation.create_response()
        else:
            recorder.write(
                "client_action",
                {
                    "event": "vad_wait",
                    "reason": "VAD 模式按官方约束不主动 commit/create response",
                    "tail_silence_seconds": args.vad_tail_seconds,
                },
            )
        time.sleep(max(0.0, args.wait_seconds))
    finally:
        recorder.write("client_action", {"event": "close"})
        conversation.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
