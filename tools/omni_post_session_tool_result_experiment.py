#!/usr/bin/env python3
"""Omni Realtime 会话结束后工具结果注入实验。

主要功能：
1. 直接连接 DashScope Qwen Omni Realtime，发送一段音频并诱导模型调用测试工具。
2. 拿到 `response.function_call_arguments.done` 的 call_id 后，按不同方式回填工具结果。
3. 记录服务端是否接受 `conversation.item.create(function_call_output)`，以及是否通过
   `response.create` 生成模型反馈。

默认实验方式：
- `same_session`：同一活跃 session 内回填工具结果并 `response.create`，作为基线。
- `closed_same_conversation`：关闭 session 后继续用原 conversation 对象回填工具结果。
- `new_session_function_output`：新建 session 后沿用旧 call_id 回填 function_call_output。

可选补充方式：
- `new_session_message_context`：新建 session 后尝试创建 message item，把工具结果作为文本上下文。
  官方文档说明 `conversation.item.create` 当前仅支持 `function_call_output`，该方式用于验证服务端行为。

late result 注入方式（验证统一 Tool Run 等待窗口方案，均在同一活跃 session 内）：
- `same_session_second_function_output`：先回填“运行中”的 function_call_output（模拟等待窗口超时），
  模型播报后再用同一 call_id 二次回填最终结果，验证服务端是否接受同 call_id 重复回填。
- `same_session_instructions_followup`：先回填“运行中”的 function_call_output，late result 到达后
  不创建 item，改用 `response.create(instructions=最终结果文本)` 驱动模型播报。
- `same_session_delayed_function_output`：等待窗口超时时不回填，延迟 `--late-delay-seconds` 秒后
  再回填原 call_id 的 function_call_output，验证 provider 对挂起 function_call 的容忍时长。

示例：
    uv run python tools/omni_post_session_tool_result_experiment.py \
      --audio 'testdata/audio-sample/帮我查一下今天的天气.wav' \
      --out-dir runs/omni-post-session-tool-result
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_MODES = ("same_session", "closed_same_conversation", "new_session_function_output")

LATE_RESULT_MODES = (
    "same_session_second_function_output",
    "same_session_instructions_followup",
    "same_session_delayed_function_output",
)


@dataclass
class ModeSummary:
    """单个实验方式的结果摘要。

    主要属性：记录是否拿到工具调用、是否发送工具结果、是否收到模型反馈以及错误事件。
    """

    mode: str
    raw_events_path: str
    tool_call_received: bool = False
    tool_call_id: str = ""
    tool_name: str = ""
    tool_result_sent: bool = False
    response_create_sent: bool = False
    feedback_received: bool = False
    feedback_texts: list[str] = field(default_factory=list)
    running_output_sent: bool = False
    followup_action: str = ""
    followup_item_sent: bool = False
    followup_response_create_sent: bool = False
    followup_feedback_received: bool = False
    followup_feedback_texts: list[str] = field(default_factory=list)
    error_events: list[str] = field(default_factory=list)
    client_errors: list[str] = field(default_factory=list)


class ModeRecorder:
    """记录单个实验方式的原始事件和摘要。

    主要方法：`write()` 写 JSONL 并更新摘要；`wait_tool_call()` 和
    `wait_feedback()` 等待关键事件。
    """

    def __init__(self, *, summary: ModeSummary) -> None:
        self.summary = summary
        self.started_at = time.monotonic()
        self.tool_call_event = threading.Event()
        self.feedback_event = threading.Event()
        self.followup_feedback_event = threading.Event()
        self.session_updated_event = threading.Event()
        self._followup_phase = False
        self._lock = threading.Lock()
        Path(summary.raw_events_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary.raw_events_path).write_text("", encoding="utf-8")

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        """写入一条 JSONL 事件并更新摘要。

        参数：`kind` 表示事件来源；`payload` 是服务端原始事件或客户端动作。
        返回值：无。
        异常情况：无。
        """

        record = {
            "t_ms": round((time.monotonic() - self.started_at) * 1000, 3),
            "kind": kind,
            **payload,
        }
        with self._lock:
            with Path(self.summary.raw_events_path).open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            self._update_summary(record)
        event_type = payload.get("type") or payload.get("event") or kind
        compact = _compact_event(payload)
        print(f"{record['t_ms']:>10.3f}ms {self.summary.mode:<28} {kind:<14} {event_type} {compact}")

    def wait_session_updated(self, timeout_seconds: float) -> bool:
        """等待 session.updated。"""

        return self.session_updated_event.wait(timeout_seconds)

    def wait_tool_call(self, timeout_seconds: float) -> bool:
        """等待工具调用完成事件。"""

        return self.tool_call_event.wait(timeout_seconds)

    def wait_feedback(self, timeout_seconds: float) -> bool:
        """等待工具结果后的模型反馈文本或音频转录。"""

        return self.feedback_event.wait(timeout_seconds)

    def mark_followup_phase(self) -> None:
        """进入 late result follow-up 阶段。

        主要逻辑：之后的 conversation.item.create、response.create 和模型反馈
        记入 follow-up 摘要字段，与等待窗口内的首次回填区分开。
        """

        with self._lock:
            self._followup_phase = True

    def wait_followup_feedback(self, timeout_seconds: float) -> bool:
        """等待 late result 注入后的模型反馈。"""

        return self.followup_feedback_event.wait(timeout_seconds)

    def _update_summary(self, record: dict[str, Any]) -> None:
        event_type = str(record.get("type") or record.get("event") or "")
        item = record.get("item") if isinstance(record.get("item"), dict) else {}
        if event_type == "session.updated":
            self.session_updated_event.set()
        if event_type in {"response.function_call_arguments.done", "response.tool_call.done"}:
            call_id = str(record.get("call_id") or item.get("call_id") or item.get("id") or "").strip()
            tool_name = str(record.get("name") or item.get("name") or "").strip()
            if call_id:
                self.summary.tool_call_received = True
                self.summary.tool_call_id = call_id
                self.summary.tool_name = tool_name
                self.tool_call_event.set()
        if record.get("kind") == "client_action" and event_type == "conversation.item.create":
            if self._followup_phase:
                self.summary.followup_item_sent = True
            else:
                self.summary.tool_result_sent = True
        if record.get("kind") == "client_action" and event_type == "response.create":
            if self._followup_phase:
                self.summary.followup_response_create_sent = True
            else:
                self.summary.response_create_sent = True
        if event_type in {"response.audio_transcript.done", "response.text.done"}:
            text = str(record.get("transcript") or record.get("text") or "").strip()
            if text:
                if self._followup_phase:
                    self.summary.followup_feedback_received = True
                    self.summary.followup_feedback_texts.append(text)
                    self.followup_feedback_event.set()
                else:
                    self.summary.feedback_received = True
                    self.summary.feedback_texts.append(text)
                    self.feedback_event.set()
        if event_type == "error":
            self.summary.error_events.append(json.dumps(record, ensure_ascii=False, default=str))
        if record.get("kind") == "client_error":
            self.summary.client_errors.append(str(record.get("message") or record))


def _compact_event(payload: dict[str, Any]) -> str:
    """提取便于终端观察的事件字段。"""

    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    response = payload.get("response") if isinstance(payload.get("response"), dict) else {}
    compact = {
        "response_id": payload.get("response_id") or response.get("id"),
        "item_id": payload.get("item_id") or item.get("id"),
        "call_id": payload.get("call_id") or item.get("call_id"),
        "item_type": item.get("type"),
        "tool_name": payload.get("name") or item.get("name"),
        "status": response.get("status"),
    }
    return json.dumps({key: value for key, value in compact.items() if value not in (None, "")}, ensure_ascii=False)


def _read_audio_bytes(path: Path) -> tuple[bytes, int]:
    """读取 16k 单声道 PCM16 音频。

    参数：`path` 为 wav 或 pcm 路径。
    返回值：PCM16 bytes 和采样率。
    异常情况：WAV 格式不是 16k/mono/16bit 时抛出 ValueError。
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


def _tool_schema(*, flat: bool) -> dict[str, Any]:
    """构造测试工具 schema。"""

    function = {
        "name": "probe_weather",
        "description": "查询指定城市的天气。用户询问天气、气温、下雨时必须调用。",
        "parameters": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "城市名称"},
            },
            "required": ["city"],
        },
    }
    if flat:
        return {"type": "function", **function}
    return {"type": "function", "function": function}


def _tool_output(tool_name: str, arguments: str) -> dict[str, Any]:
    """构造测试工具输出。"""

    return {
        "ok": True,
        "tool_name": tool_name or "probe_weather",
        "arguments": arguments,
        "weather": {"city": "上海", "condition": "晴", "temperature_c": 26},
        "message": "工具结果：上海当前天气晴，26 摄氏度。",
    }


def _new_conversation(*, args: argparse.Namespace, recorder: ModeRecorder):
    """创建并连接一个 OmniRealtimeConversation。

    参数：`args` 为命令行参数；`recorder` 接收回调事件。
    返回值：已 connect 并 update_session 的 conversation。
    异常情况：DashScope SDK 抛出的连接异常向上传递。
    """

    import dashscope
    from dashscope.audio.qwen_omni import AudioFormat, MultiModality, OmniRealtimeCallback, OmniRealtimeConversation

    class Callback(OmniRealtimeCallback):
        """DashScope Omni 回调。"""

        def on_open(self) -> None:
            recorder.write("socket", {"event": "opened"})

        def on_close(self, close_status_code: Any, close_msg: Any) -> None:
            recorder.write("socket", {"event": "closed", "code": close_status_code, "message": str(close_msg)})

        def on_event(self, message: dict[str, Any]) -> None:
            recorder.write("server_event", message)

    dashscope.api_key = args.api_key
    conversation = OmniRealtimeConversation(
        model=args.model,
        callback=Callback(),
        url=args.url,
    )
    conversation.connect()
    conversation.update_session(
        output_modalities=[MultiModality.TEXT, MultiModality.AUDIO],
        voice=args.voice,
        input_audio_format=AudioFormat.PCM_16000HZ_MONO_16BIT,
        output_audio_format=AudioFormat.PCM_24000HZ_MONO_16BIT,
        enable_input_audio_transcription=True,
        input_audio_transcription_model="paraformer-realtime-v2",
        enable_turn_detection=True,
        turn_detection_type=args.turn_detection,
        instructions=(
            "你是中文语音助手。用户询问天气时必须调用 probe_weather 工具；"
            "拿到工具结果后，用一句中文口语回答天气结果。"
        ),
        tools=[_tool_schema(flat=args.schema == "flat")],
    )
    recorder.wait_session_updated(args.wait_session_updated_seconds)
    return conversation


def _send_audio_and_wait_tool_call(*, conversation: Any, recorder: ModeRecorder, args: argparse.Namespace, audio_chunks: list[bytes]) -> bool:
    """发送音频并等待工具调用完成。"""

    recorder.write("client_action", {"event": "audio.start", "chunk_count": len(audio_chunks)})
    for chunk in audio_chunks:
        if chunk:
            conversation.append_audio(base64.b64encode(chunk).decode("ascii"))
        time.sleep(args.sleep_ms / 1000)
    if args.vad_tail_seconds > 0:
        conversation.append_audio(base64.b64encode(b"\x00" * int(16000 * 2 * args.vad_tail_seconds)).decode("ascii"))
    recorder.write("client_action", {"event": "audio.sent"})
    return recorder.wait_tool_call(args.wait_tool_call_seconds)


def _safe_call(recorder: ModeRecorder, label: str, fn) -> bool:
    """调用 SDK 方法并记录异常。"""

    try:
        fn()
        return True
    except Exception as exc:  # noqa: BLE001 - 实验需要记录 provider/SDK 真实异常
        recorder.write("client_error", {"event": label, "message": f"{type(exc).__name__}: {exc}"})
        return False


def _send_function_output_and_response(*, conversation: Any, recorder: ModeRecorder, call_id: str, tool_name: str, arguments: str) -> None:
    """发送 function_call_output 并请求模型响应。"""

    output = _tool_output(tool_name, arguments)
    recorder.write("client_action", {"event": "conversation.item.create", "call_id": call_id, "tool_name": tool_name, "output": output})
    sent = _safe_call(
        recorder,
        "conversation.item.create.failed",
        lambda: conversation.create_item(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(output, ensure_ascii=False),
            }
        ),
    )
    if not sent:
        return
    recorder.write("client_action", {"event": "response.create", "reason": "after_function_call_output"})
    _safe_call(recorder, "response.create.failed", lambda: conversation.create_response())


def _running_tool_output(tool_name: str) -> dict[str, Any]:
    """构造“工具已启动仍在处理”的结构化结果，模拟等待窗口超时。"""

    return {
        "ok": True,
        "status": "running",
        "tool_name": tool_name or "probe_weather",
        "tool_run_id": "tool_run_experiment_001",
        "message": "工具已启动，仍在后台查询中，结果稍后送达。请先告诉用户正在查询。",
    }


def _send_running_output_and_response(*, conversation: Any, recorder: ModeRecorder, call_id: str, tool_name: str) -> None:
    """回填“运行中”的 function_call_output 并请求模型先行播报。"""

    output = _running_tool_output(tool_name)
    recorder.summary.running_output_sent = True
    recorder.write("client_action", {"event": "conversation.item.create", "call_id": call_id, "tool_name": tool_name, "output": output})
    sent = _safe_call(
        recorder,
        "conversation.item.create.running.failed",
        lambda: conversation.create_item(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(output, ensure_ascii=False),
            }
        ),
    )
    if not sent:
        return
    recorder.write("client_action", {"event": "response.create", "reason": "after_running_function_call_output"})
    _safe_call(recorder, "response.create.failed", lambda: conversation.create_response())


def _run_late_result_mode(
    *,
    mode: str,
    conversation: Any,
    recorder: ModeRecorder,
    args: argparse.Namespace,
    call_id: str,
    tool_name: str,
    arguments: str,
) -> None:
    """在同一活跃 session 内验证 late result 注入方式。

    主要逻辑：
    1. `same_session_delayed_function_output` 不做窗口内回填，延迟后直接回填原 call_id。
    2. 其余方式先回填“运行中”结果并等模型播报，再按方式注入最终结果。
    参数：`mode` 为 late result 实验方式；其余为当前会话上下文。
    返回值：无，结论写入 recorder.summary。
    异常情况：SDK 异常由 `_safe_call` 记录，不向上抛出。
    """

    recorder.summary.followup_action = mode
    if mode == "same_session_delayed_function_output":
        recorder.write(
            "client_action",
            {"event": "late_result.wait", "delay_seconds": args.late_delay_seconds, "reason": "simulate_slow_tool_without_backfill"},
        )
        time.sleep(args.late_delay_seconds)
        recorder.mark_followup_phase()
        _send_function_output_and_response(
            conversation=conversation,
            recorder=recorder,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
        recorder.wait_followup_feedback(args.wait_feedback_seconds)
        return
    _send_running_output_and_response(conversation=conversation, recorder=recorder, call_id=call_id, tool_name=tool_name)
    recorder.wait_feedback(args.wait_feedback_seconds)
    recorder.write("client_action", {"event": "late_result.settle", "delay_seconds": args.followup_gap_seconds})
    time.sleep(args.followup_gap_seconds)
    recorder.mark_followup_phase()
    if mode == "same_session_second_function_output":
        _send_function_output_and_response(
            conversation=conversation,
            recorder=recorder,
            call_id=call_id,
            tool_name=tool_name,
            arguments=arguments,
        )
    elif mode == "same_session_instructions_followup":
        output = _tool_output(tool_name, arguments)
        instructions = (
            f"刚才的 {tool_name or 'probe_weather'} 工具已经在后台完成，最终结果："
            f"{output['message']}请用一句中文口语把这个结果告诉用户，不要提工具内部细节。"
        )
        recorder.write("client_action", {"event": "response.create", "reason": "late_result_instructions", "instructions": instructions})
        _safe_call(
            recorder,
            "response.create.instructions.failed",
            lambda: conversation.create_response(instructions=instructions),
        )
    else:
        recorder.write("client_error", {"event": "unknown_mode", "message": mode})
        return
    recorder.wait_followup_feedback(args.wait_feedback_seconds)


def _send_message_context_and_response(*, conversation: Any, recorder: ModeRecorder, call_id: str, tool_name: str, arguments: str) -> None:
    """尝试把工具结果作为 message item 放入新 session。"""

    text = (
        f"上一轮会话中的工具 {tool_name or 'probe_weather'}，call_id={call_id}，"
        f"参数={arguments}，工具结果：上海当前天气晴，26 摄氏度。请基于这个工具结果回答用户。"
    )
    item = {"type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}
    recorder.write("client_action", {"event": "conversation.item.create", "item": item})
    sent = _safe_call(recorder, "conversation.item.create.message.failed", lambda: conversation.create_item(item))
    if not sent:
        return
    recorder.write("client_action", {"event": "response.create", "reason": "after_message_context"})
    _safe_call(recorder, "response.create.failed", lambda: conversation.create_response())


def run_mode(*, mode: str, args: argparse.Namespace, audio_chunks: list[bytes]) -> ModeSummary:
    """执行一种实验方式。"""

    summary = ModeSummary(mode=mode, raw_events_path=str(args.out_dir / f"{mode}.jsonl"))
    recorder = ModeRecorder(summary=summary)
    conversation = None
    try:
        recorder.write("client_action", {"event": "mode.start", "schema": args.schema})
        conversation = _new_conversation(args=args, recorder=recorder)
        got_tool_call = _send_audio_and_wait_tool_call(conversation=conversation, recorder=recorder, args=args, audio_chunks=audio_chunks)
        if not got_tool_call:
            recorder.write("client_error", {"event": "tool_call.timeout", "message": "未等到工具调用"})
            return summary
        call_id = summary.tool_call_id
        tool_name = summary.tool_name
        arguments = ""
        if mode == "same_session":
            _send_function_output_and_response(
                conversation=conversation,
                recorder=recorder,
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            recorder.wait_feedback(args.wait_feedback_seconds)
            return summary
        if mode in LATE_RESULT_MODES:
            _run_late_result_mode(
                mode=mode,
                conversation=conversation,
                recorder=recorder,
                args=args,
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            return summary
        recorder.write("client_action", {"event": "conversation.close", "reason": "before_tool_result"})
        _safe_call(recorder, "conversation.close.failed", conversation.close)
        time.sleep(args.after_close_delay_seconds)
        if mode == "closed_same_conversation":
            _send_function_output_and_response(
                conversation=conversation,
                recorder=recorder,
                call_id=call_id,
                tool_name=tool_name,
                arguments=arguments,
            )
            recorder.wait_feedback(args.wait_feedback_seconds)
            return summary
        new_summary = summary
        new_recorder = recorder
        new_recorder.write("client_action", {"event": "new_conversation.open", "mode": mode})
        new_conversation = _new_conversation(args=args, recorder=new_recorder)
        try:
            if mode == "new_session_function_output":
                _send_function_output_and_response(
                    conversation=new_conversation,
                    recorder=new_recorder,
                    call_id=call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            elif mode == "new_session_message_context":
                _send_message_context_and_response(
                    conversation=new_conversation,
                    recorder=new_recorder,
                    call_id=call_id,
                    tool_name=tool_name,
                    arguments=arguments,
                )
            else:
                new_recorder.write("client_error", {"event": "unknown_mode", "message": mode})
            new_recorder.wait_feedback(args.wait_feedback_seconds)
        finally:
            _safe_call(new_recorder, "new_conversation.close.failed", new_conversation.close)
        return new_summary
    finally:
        if conversation is not None and (mode == "same_session" or mode in LATE_RESULT_MODES):
            _safe_call(recorder, "conversation.close.failed", conversation.close)


def _build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数。"""

    parser = argparse.ArgumentParser(description="实验 session 结束后 Omni Realtime 工具结果注入方式。")
    parser.add_argument("--audio", type=Path, default=Path("testdata/audio-sample/帮我查一下今天的天气.wav"))
    parser.add_argument("--out-dir", type=Path, default=Path("runs/omni-post-session-tool-result"))
    parser.add_argument("--model", default="qwen3.5-omni-plus-realtime")
    parser.add_argument("--voice", default="Tina")
    parser.add_argument("--url", default="wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
    parser.add_argument("--schema", choices=["flat", "nested"], default="flat")
    parser.add_argument("--turn-detection", choices=["semantic_vad", "server_vad"], default="semantic_vad")
    parser.add_argument("--chunk-ms", type=int, default=20)
    parser.add_argument("--sleep-ms", type=int, default=20)
    parser.add_argument("--vad-tail-seconds", type=float, default=1.2)
    parser.add_argument("--wait-session-updated-seconds", type=float, default=5)
    parser.add_argument("--wait-tool-call-seconds", type=float, default=18)
    parser.add_argument("--wait-feedback-seconds", type=float, default=12)
    parser.add_argument("--after-close-delay-seconds", type=float, default=0.5)
    parser.add_argument("--late-delay-seconds", type=float, default=15, help="delayed_function_output 方式中延迟回填的秒数")
    parser.add_argument("--followup-gap-seconds", type=float, default=2.0, help="运行中播报后到 late result 注入之间的间隔秒数")
    parser.add_argument("--modes", nargs="+", default=list(DEFAULT_MODES), help="要运行的实验方式")
    parser.add_argument("--include-message-context", action="store_true", help="额外测试新 session message item 方式")
    return parser


def main() -> int:
    """运行实验并写 summary。"""

    args = _build_arg_parser().parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)
    api_key = os.getenv("DASHSCOPE_API_KEY_OMNI_CAP") or os.getenv("DASHSCOPE_API_KEY")
    if not api_key:
        print("缺少 DASHSCOPE_API_KEY_OMNI_CAP 或 DASHSCOPE_API_KEY，无法运行真实 Omni 实验。", file=sys.stderr)
        return 2
    args.api_key = api_key
    try:
        import dashscope  # noqa: F401
        import dashscope.audio.qwen_omni  # noqa: F401
    except ImportError as exc:
        print(f"缺少 dashscope SDK：{exc}", file=sys.stderr)
        return 2
    audio_bytes, sample_rate = _read_audio_bytes(args.audio)
    audio_chunks = _chunk_bytes(audio_bytes, sample_rate=sample_rate, chunk_ms=args.chunk_ms)
    modes = list(args.modes)
    if args.include_message_context and "new_session_message_context" not in modes:
        modes.append("new_session_message_context")
    summaries = [run_mode(mode=mode, args=args, audio_chunks=audio_chunks) for mode in modes]
    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps([asdict(item) for item in summaries], ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"summary_path={summary_path}")
    for item in summaries:
        line = (
            f"{item.mode}: tool_call={item.tool_call_received} sent={item.tool_result_sent} "
            f"response_create={item.response_create_sent} feedback={item.feedback_received} "
            f"errors={len(item.error_events) + len(item.client_errors)}"
        )
        if item.followup_action:
            line += (
                f" | followup: item_sent={item.followup_item_sent} "
                f"response_create={item.followup_response_create_sent} "
                f"feedback={item.followup_feedback_received}"
            )
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
