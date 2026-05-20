#!/usr/bin/env python3
"""Omni Realtime 工具调用稳定性探针。

主要功能：
1. 直接连接 DashScope Omni Realtime，不经过 realtime-agent server。
2. 使用同一段用户音频和同一个 `start_find_object_task` 工具 schema 做多轮重复测试。
3. 统计每轮是否真正产生 function call、是否先输出普通音频、是否出现“已启动任务”但没有工具调用。
4. 生成逐轮 JSONL 原始事件和汇总 JSON，便于判断 Omni 工具调用不稳定是否真实存在。

使用前提：
- 已安装本仓库依赖：`uv sync --python 3.11`
- 已设置 `DASHSCOPE_API_KEY`

示例：
    uv run python tools/omni_tool_call_stability_probe.py \
      --audio 'testdata/audio-sample/帮我找一下手机在哪里.wav' \
      --repeats 5 \
      --schema flat \
      --instructions-mode both \
      --out-dir runs/omni-tool-call-stability
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import threading
import time
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable


START_CLAIM_PATTERN = re.compile(r"(已经|已|帮你|为你).{0,12}(启动|开始).{0,12}(找物|找手机|任务|后台)")
TOOL_SPEECH_PATTERN = re.compile(r"(start_find_object_task|function|函数|工具|tool|调用)")


def _json_default(value: Any) -> str:
    """把 DashScope SDK 对象转为可写入 JSON 的字符串。"""

    return str(value)


@dataclass
class ProbeRunSummary:
    """单轮 Omni 工具调用探针汇总。

    主要属性：
    - `tool_call_count`：本轮收到的 function call 完成数量。
    - `audio_delta_count`：本轮收到的输出音频 delta 数量。
    - `audio_before_tool`：第一段音频是否早于第一条工具调用。
    - `audio_during_tool`：工具调用已发起但工具结果未回填期间是否输出音频。
    - `audio_after_tool_result`：工具结果回填后是否输出音频。
    - `start_claim_without_tool`：模型是否声称已启动任务但本轮没有工具调用。
    """

    run_id: str
    schema: str
    instructions_mode: str
    turn_detection: str
    raw_events_path: str
    session_updated: bool = False
    response_created_count: int = 0
    message_item_count: int = 0
    function_item_count: int = 0
    tool_call_count: int = 0
    audio_delta_count: int = 0
    transcript_delta_count: int = 0
    first_message_item_ms: float | None = None
    first_function_item_ms: float | None = None
    first_tool_done_ms: float | None = None
    tool_result_output_sent_ms: float | None = None
    tool_result_response_create_ms: float | None = None
    first_audio_delta_ms: float | None = None
    first_transcript_delta_ms: float | None = None
    function_names: list[str] = field(default_factory=list)
    transcript_done_texts: list[str] = field(default_factory=list)
    before_tool_transcript_delta_text: str = ""
    during_tool_transcript_delta_text: str = ""
    after_tool_result_transcript_delta_text: str = ""
    before_tool_transcript_texts: list[str] = field(default_factory=list)
    during_tool_transcript_texts: list[str] = field(default_factory=list)
    after_tool_result_transcript_texts: list[str] = field(default_factory=list)
    start_claim_texts: list[str] = field(default_factory=list)
    tool_speech_texts: list[str] = field(default_factory=list)
    before_tool_audio_delta_count: int = 0
    during_tool_audio_delta_count: int = 0
    after_tool_result_audio_delta_count: int = 0
    audio_before_tool: bool = False
    message_before_tool: bool = False
    audio_during_tool: bool = False
    audio_after_tool_result: bool = False
    transcript_after_tool_result: bool = False
    tool_speech_during_tool: bool = False
    start_claim_without_tool: bool = False
    error_events: list[str] = field(default_factory=list)

    @property
    def has_tool_call(self) -> bool:
        """返回本轮是否至少收到一次工具调用完成事件。"""

        return self.tool_call_count > 0


class RunRecorder:
    """记录单轮原始事件并维护统计字段。

    主要方法：
    - `write()`：写入 JSONL 原始事件，并更新统计。
    - `maybe_send_tool_output()`：可选回填 function_call_output，观察工具后续响应。
    """

    def __init__(self, *, summary: ProbeRunSummary, auto_tool_result: bool, tool_result_delay_seconds: float) -> None:
        self.started_at = time.monotonic()
        self.summary = summary
        self.auto_tool_result = auto_tool_result
        self.tool_result_delay_seconds = max(0.0, tool_result_delay_seconds)
        self.conversation: Any | None = None
        self.function_outputs_sent: set[str] = set()
        self.session_updated = threading.Event()
        self._write_lock = threading.Lock()
        Path(summary.raw_events_path).parent.mkdir(parents=True, exist_ok=True)
        Path(summary.raw_events_path).write_text("", encoding="utf-8")

    def bind_conversation(self, conversation: Any) -> None:
        """绑定 DashScope conversation，供工具结果回填使用。"""

        self.conversation = conversation

    def write(self, kind: str, payload: dict[str, Any]) -> None:
        """写一条 JSONL 事件并更新单轮汇总。

        参数：
        - `kind`：事件来源，例如 `server_event`、`client_action`。
        - `payload`：原始事件或派生事件。
        """

        record = {
            "t_ms": round((time.monotonic() - self.started_at) * 1000, 3),
            "kind": kind,
            **payload,
        }
        with self._write_lock:
            with Path(self.summary.raw_events_path).open("a", encoding="utf-8") as fp:
                fp.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
            self._update_summary(record)
        event_type = payload.get("type") or payload.get("event") or kind
        compact = _event_compact(payload)
        print(f"{record['t_ms']:>10.3f}ms {self.summary.run_id:<20} {kind:<14} {event_type} {compact}")

    def maybe_send_tool_output(self, message: dict[str, Any]) -> None:
        """收到 function call 完成事件后，可选回填工具结果。

        主要逻辑：同一个 `call_id` 只回填一次；回填内容只用于让 provider
        结束工具等待状态，不代表真实业务 Task 已执行。
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
        if not call_id or call_id in self.function_outputs_sent:
            return
        self.function_outputs_sent.add(call_id)
        thread = threading.Thread(
            target=self._send_tool_output_after_delay,
            kwargs={"call_id": call_id, "tool_name": tool_name},
            name=f"omni-probe-tool-output-{call_id}",
            daemon=True,
        )
        thread.start()

    def _send_tool_output_after_delay(self, *, call_id: str, tool_name: str) -> None:
        """在后台线程延迟回填工具结果，避免阻塞 DashScope 事件回调。"""

        if self.conversation is None:
            return
        if self.tool_result_delay_seconds > 0:
            self.write(
                "client_action",
                {
                    "event": "tool_result.delay",
                    "call_id": call_id,
                    "tool_name": tool_name,
                    "seconds": self.tool_result_delay_seconds,
                },
            )
            time.sleep(self.tool_result_delay_seconds)
        output = {
            "ok": True,
            "tool_name": tool_name,
            "task_id": "probe_task_001",
            "state": "started",
            "message": "工具结果：找物任务已经启动成功。",
            "probe_note": "稳定性探针自动回填；代表本轮测试中的工具启动成功。",
        }
        self.write(
            "client_action",
            {
                "event": "conversation.item.create",
                "call_id": call_id,
                "tool_name": tool_name,
                "output": output,
            },
        )
        self.conversation.create_item(
            {
                "type": "function_call_output",
                "call_id": call_id,
                "output": json.dumps(output, ensure_ascii=False),
            }
        )
        self.write("client_action", {"event": "response.create", "reason": "after_function_call_output"})
        self.conversation.create_response()

    def _update_summary(self, record: dict[str, Any]) -> None:
        """根据一条事件更新统计字段。"""

        event_type = str(record.get("type") or record.get("event") or "")
        t_ms = float(record.get("t_ms") or 0.0)
        item = record.get("item") if isinstance(record.get("item"), dict) else {}
        response = record.get("response") if isinstance(record.get("response"), dict) else {}
        if event_type == "session.updated":
            self.summary.session_updated = True
            self.session_updated.set()
        if record.get("kind") == "client_action" and event_type == "conversation.item.create":
            self.summary.tool_result_output_sent_ms = self.summary.tool_result_output_sent_ms or t_ms
        if record.get("kind") == "client_action" and event_type == "response.create":
            self.summary.tool_result_response_create_ms = self.summary.tool_result_response_create_ms or t_ms
        if event_type == "response.created":
            self.summary.response_created_count += 1
        if event_type == "response.output_item.added":
            item_type = str(item.get("type") or "")
            if item_type == "message":
                self.summary.message_item_count += 1
                self.summary.first_message_item_ms = self.summary.first_message_item_ms or t_ms
            if item_type in {"function_call", "tool_call"}:
                self.summary.function_item_count += 1
                self.summary.first_function_item_ms = self.summary.first_function_item_ms or t_ms
                name = str(item.get("name") or "").strip()
                if name:
                    self.summary.function_names.append(name)
        if event_type in {"response.function_call_arguments.done", "response.tool_call.done"}:
            self.summary.tool_call_count += 1
            self.summary.first_tool_done_ms = self.summary.first_tool_done_ms or t_ms
            name = str(record.get("name") or item.get("name") or "").strip()
            if name:
                self.summary.function_names.append(name)
        if event_type == "response.audio.delta":
            self.summary.audio_delta_count += 1
            self.summary.first_audio_delta_ms = self.summary.first_audio_delta_ms or t_ms
            phase = self._phase_at(t_ms)
            if phase == "before_tool":
                self.summary.before_tool_audio_delta_count += 1
            elif phase == "during_tool":
                self.summary.during_tool_audio_delta_count += 1
            else:
                self.summary.after_tool_result_audio_delta_count += 1
        if event_type == "response.audio_transcript.delta":
            self.summary.transcript_delta_count += 1
            self.summary.first_transcript_delta_ms = self.summary.first_transcript_delta_ms or t_ms
            text = str(record.get("delta") or "")
            phase = self._phase_at(t_ms)
            if phase == "before_tool":
                self.summary.before_tool_transcript_delta_text += text
            elif phase == "during_tool":
                self.summary.during_tool_transcript_delta_text += text
            else:
                self.summary.after_tool_result_transcript_delta_text += text
        if event_type == "response.audio_transcript.done":
            text = str(record.get("transcript") or record.get("text") or "").strip()
            if text:
                self.summary.transcript_done_texts.append(text)
                phase = self._phase_at(t_ms)
                if phase == "before_tool":
                    self.summary.before_tool_transcript_texts.append(text)
                elif phase == "during_tool":
                    self.summary.during_tool_transcript_texts.append(text)
                else:
                    self.summary.after_tool_result_transcript_texts.append(text)
                if START_CLAIM_PATTERN.search(text):
                    self.summary.start_claim_texts.append(text)
                if TOOL_SPEECH_PATTERN.search(text):
                    self.summary.tool_speech_texts.append(text)
        if event_type == "response.done" and isinstance(response, dict):
            status = str(response.get("status") or "")
            if status and status != "completed":
                self.summary.error_events.append(f"response.done status={status}")
        if event_type in {"error", "conversation.error"}:
            self.summary.error_events.append(json.dumps(record, ensure_ascii=False, default=_json_default))

    def _phase_at(self, t_ms: float) -> str:
        """按事件时间判断当前输出属于工具调用的哪个阶段。"""

        if self.summary.first_function_item_ms is None or t_ms < self.summary.first_function_item_ms:
            return "before_tool"
        if self.summary.tool_result_output_sent_ms is None or t_ms < self.summary.tool_result_output_sent_ms:
            return "during_tool"
        return "after_tool_result"

    def finalize(self) -> None:
        """根据已收集的首事件时间补齐派生结论。"""

        first_tool_ms = self.summary.first_tool_done_ms or self.summary.first_function_item_ms
        first_audio_ms = self.summary.first_audio_delta_ms
        first_message_ms = self.summary.first_message_item_ms
        self.summary.audio_before_tool = bool(first_audio_ms is not None and (first_tool_ms is None or first_audio_ms < first_tool_ms))
        self.summary.message_before_tool = bool(
            first_message_ms is not None and (first_tool_ms is None or first_message_ms < first_tool_ms)
        )
        self.summary.audio_during_tool = self.summary.during_tool_audio_delta_count > 0
        self.summary.audio_after_tool_result = self.summary.after_tool_result_audio_delta_count > 0
        self.summary.transcript_after_tool_result = bool(self.summary.after_tool_result_transcript_texts)
        tool_speech_text = "\n".join(
            [
                self.summary.before_tool_transcript_delta_text,
                self.summary.during_tool_transcript_delta_text,
                "\n".join(self.summary.before_tool_transcript_texts),
                "\n".join(self.summary.during_tool_transcript_texts),
            ]
        )
        self.summary.tool_speech_during_tool = bool(TOOL_SPEECH_PATTERN.search(tool_speech_text))
        self.summary.start_claim_without_tool = bool(self.summary.start_claim_texts and self.summary.tool_call_count == 0)


def _event_compact(message: dict[str, Any]) -> str:
    """提取便于人工扫日志的关键字段。"""

    item = message.get("item") if isinstance(message.get("item"), dict) else {}
    response = message.get("response") if isinstance(message.get("response"), dict) else {}
    values = {
        "response_id": message.get("response_id") or response.get("id"),
        "item_id": message.get("item_id") or item.get("id"),
        "call_id": message.get("call_id") or item.get("call_id"),
        "item_type": item.get("type"),
        "name": message.get("name") or item.get("name"),
        "status": response.get("status"),
    }
    compact = {key: value for key, value in values.items() if value not in (None, "")}
    return json.dumps(compact, ensure_ascii=False)


def _read_audio_bytes(path: Path) -> tuple[bytes, int]:
    """读取 16k 单声道 PCM16 音频。

    异常情况：
    - WAV 不是 16kHz、单声道、16bit PCM 时抛出 `ValueError`。
    - `.pcm` 默认认为已经是 16kHz 单声道 PCM16。
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


def _tool_schema(*, schema: str) -> list[dict[str, Any]]:
    """构造 `start_find_object_task` 工具 schema。

    参数：
    - `schema=flat`：使用当前 realtime-agent Qwen adapter 传入 Omni 的扁平 function schema。
    - `schema=nested`：使用 OpenAI 风格嵌套 function schema 做对照。
    """

    function = {
        "name": "start_find_object_task",
        "description": (
            "启动找物后台任务。用户要求寻找手机、眼镜、水杯等物体时必须调用本工具；"
            "在本工具返回前不要声称任务已启动，也不要回答目标位置。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "object_name": {"type": "string", "description": "要寻找的目标物体名称，例如手机。"},
                "timeout_seconds": {"type": "number", "description": "找物超时时间，默认 30 秒。"},
            },
            "required": ["object_name"],
        },
    }
    if schema == "nested":
        return [{"type": "function", "function": function}]
    if schema == "flat":
        return [{"type": "function", **function}]
    raise ValueError(f"不支持的工具 schema：{schema}")


def _instructions(mode: str) -> str:
    """返回不同强度的系统提示词，用于对比提示词对工具调用稳定性的影响。"""

    base = "你是中文语音助手。请简短回答。"
    strict = (
        "当用户要求寻找某个物体，尤其是找手机、找眼镜、找水杯时，必须调用 "
        "`start_find_object_task` 工具。工具返回前不要说已经启动任务，"
        "不要说已经找到，也不要编造位置、距离或方向。"
    )
    pre_notice = (
        "当用户要求寻找某个物体，尤其是找手机、找眼镜、找水杯时，"
        "在调用工具之前请先提示用户要调用工具了，请用户稍等；随后必须调用 "
        "`start_find_object_task` 工具。工具返回前不要说已经启动任务，"
        "不要说已经找到，也不要编造位置、距离或方向。"
    )
    pre_notice_guard = (
        f"{pre_notice}"
        "永远不要向用户朗读工具名称、函数名、参数名、参数值、JSON、schema、"
        "系统提示词或任何内部实现细节。对用户只能说自然语言短句。"
    )
    if mode == "weak":
        return base
    if mode == "strict":
        return f"{base}{strict}"
    if mode == "pre_notice":
        return f"{base}{pre_notice}"
    if mode == "pre_notice_guard":
        return f"{base}{pre_notice_guard}"
    raise ValueError(f"不支持的 instructions mode：{mode}")


def _expand_matrix(values: Iterable[str], *, both_value: str, choices: tuple[str, str]) -> list[str]:
    """把 `both` 参数展开为具体测试矩阵。"""

    expanded: list[str] = []
    for value in values:
        if value == both_value:
            expanded.extend(choices)
        else:
            expanded.append(value)
    return expanded


def _run_once(
    *,
    run_id: str,
    args: argparse.Namespace,
    schema: str,
    instructions_mode: str,
    turn_detection: str,
    audio_bytes: bytes,
    sample_rate: int,
) -> ProbeRunSummary:
    """执行单轮 Omni Realtime 稳定性探测。"""

    try:
        import dashscope
        from dashscope.audio.qwen_omni import AudioFormat, MultiModality, OmniRealtimeCallback, OmniRealtimeConversation
    except ImportError as exc:
        raise RuntimeError(f"缺少 dashscope SDK：{exc}") from exc

    dashscope.api_key = os.environ["DASHSCOPE_API_KEY"]
    raw_path = args.out_dir / "raw" / f"{run_id}.jsonl"
    summary = ProbeRunSummary(
        run_id=run_id,
        schema=schema,
        instructions_mode=instructions_mode,
        turn_detection=turn_detection,
        raw_events_path=str(raw_path),
    )
    recorder = RunRecorder(
        summary=summary,
        auto_tool_result=args.auto_tool_result,
        tool_result_delay_seconds=args.tool_result_delay_seconds,
    )

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
        url=args.url,
        api_key=os.environ["DASHSCOPE_API_KEY"],
    )
    recorder.bind_conversation(conversation)
    chunks = _chunk_bytes(audio_bytes, sample_rate=sample_rate, chunk_ms=args.chunk_ms)
    try:
        recorder.write("client_action", {"event": "connect"})
        conversation.connect()
        enable_turn_detection = turn_detection != "manual"
        update_kwargs: dict[str, Any] = {
            "output_modalities": [MultiModality.TEXT, MultiModality.AUDIO],
            "voice": args.voice,
            "input_audio_format": AudioFormat.PCM_16000HZ_MONO_16BIT,
            "output_audio_format": AudioFormat.PCM_24000HZ_MONO_16BIT,
            "enable_input_audio_transcription": True,
            "input_audio_transcription_model": args.input_transcription_model,
            "enable_turn_detection": enable_turn_detection,
            "instructions": _instructions(instructions_mode),
            "tools": _tool_schema(schema=schema),
        }
        if enable_turn_detection:
            update_kwargs["turn_detection_type"] = turn_detection
        recorder.write(
            "client_action",
            {
                "event": "session.update",
                "schema": schema,
                "instructions_mode": instructions_mode,
                "turn_detection": turn_detection,
                "tool_count": 1,
            },
        )
        conversation.update_session(**update_kwargs)
        recorder.session_updated.wait(timeout=args.session_update_timeout)
        send_chunks = list(chunks)
        if enable_turn_detection and args.vad_tail_seconds > 0:
            send_chunks.append(b"\x00" * int(16000 * 2 * args.vad_tail_seconds))
        for index, chunk in enumerate(send_chunks):
            conversation.append_audio(base64.b64encode(chunk).decode("ascii"))
            if index == 0 or index == len(send_chunks) - 1:
                recorder.write("client_action", {"event": "input_audio_buffer.append", "seq": index, "bytes": len(chunk)})
            time.sleep(max(0, args.sleep_ms) / 1000)
        if turn_detection == "manual":
            recorder.write("client_action", {"event": "input_audio_buffer.commit"})
            conversation.commit()
            recorder.write("client_action", {"event": "response.create", "reason": "manual_turn"})
            conversation.create_response()
        else:
            recorder.write("client_action", {"event": "vad_wait", "tail_silence_seconds": args.vad_tail_seconds})
        time.sleep(max(0.0, args.wait_seconds))
    finally:
        recorder.write("client_action", {"event": "close"})
        conversation.close()
        recorder.finalize()
    return summary


def _aggregate(summaries: list[ProbeRunSummary]) -> dict[str, Any]:
    """聚合多轮探针结果，输出便于判断稳定性的统计。"""

    total = len(summaries)
    missing_tool = [item for item in summaries if not item.has_tool_call]
    audio_before_tool = [item for item in summaries if item.audio_before_tool]
    audio_during_tool = [item for item in summaries if item.audio_during_tool]
    audio_after_tool_result = [item for item in summaries if item.audio_after_tool_result]
    transcript_after_tool_result = [item for item in summaries if item.transcript_after_tool_result]
    message_before_tool = [item for item in summaries if item.message_before_tool]
    missing_after_tool_result_audio = [
        item for item in summaries if item.tool_result_output_sent_ms is not None and not item.audio_after_tool_result
    ]
    start_claim_without_tool = [item for item in summaries if item.start_claim_without_tool]
    tool_speech_during_tool = [item for item in summaries if item.tool_speech_during_tool]
    by_case: dict[str, dict[str, Any]] = {}
    for item in summaries:
        key = f"schema={item.schema}|instructions={item.instructions_mode}|turn={item.turn_detection}"
        bucket = by_case.setdefault(
            key,
            {
                "total": 0,
                "tool_call_count": 0,
                "missing_tool_count": 0,
                "audio_before_tool_count": 0,
                "audio_during_tool_count": 0,
                "audio_after_tool_result_count": 0,
                "transcript_after_tool_result_count": 0,
                "missing_after_tool_result_audio_count": 0,
                "message_before_tool_count": 0,
                "tool_speech_during_tool_count": 0,
                "start_claim_without_tool_count": 0,
            },
        )
        bucket["total"] += 1
        bucket["tool_call_count"] += 1 if item.has_tool_call else 0
        bucket["missing_tool_count"] += 1 if not item.has_tool_call else 0
        bucket["audio_before_tool_count"] += 1 if item.audio_before_tool else 0
        bucket["audio_during_tool_count"] += 1 if item.audio_during_tool else 0
        bucket["audio_after_tool_result_count"] += 1 if item.audio_after_tool_result else 0
        bucket["transcript_after_tool_result_count"] += 1 if item.transcript_after_tool_result else 0
        bucket["missing_after_tool_result_audio_count"] += (
            1 if item.tool_result_output_sent_ms is not None and not item.audio_after_tool_result else 0
        )
        bucket["message_before_tool_count"] += 1 if item.message_before_tool else 0
        bucket["tool_speech_during_tool_count"] += 1 if item.tool_speech_during_tool else 0
        bucket["start_claim_without_tool_count"] += 1 if item.start_claim_without_tool else 0
    return {
        "total_runs": total,
        "tool_call_runs": total - len(missing_tool),
        "missing_tool_runs": len(missing_tool),
        "tool_call_rate": (total - len(missing_tool)) / total if total else 0.0,
        "audio_before_tool_runs": len(audio_before_tool),
        "audio_during_tool_runs": len(audio_during_tool),
        "audio_after_tool_result_runs": len(audio_after_tool_result),
        "transcript_after_tool_result_runs": len(transcript_after_tool_result),
        "missing_after_tool_result_audio_runs": len(missing_after_tool_result_audio),
        "message_before_tool_runs": len(message_before_tool),
        "tool_speech_during_tool_runs": len(tool_speech_during_tool),
        "start_claim_without_tool_runs": len(start_claim_without_tool),
        "missing_tool_run_ids": [item.run_id for item in missing_tool],
        "audio_before_tool_run_ids": [item.run_id for item in audio_before_tool],
        "audio_during_tool_run_ids": [item.run_id for item in audio_during_tool],
        "audio_after_tool_result_run_ids": [item.run_id for item in audio_after_tool_result],
        "missing_after_tool_result_audio_run_ids": [item.run_id for item in missing_after_tool_result_audio],
        "tool_speech_during_tool_run_ids": [item.run_id for item in tool_speech_during_tool],
        "start_claim_without_tool_run_ids": [item.run_id for item in start_claim_without_tool],
        "by_case": by_case,
    }


def _build_arg_parser() -> argparse.ArgumentParser:
    """构建命令行参数。"""

    parser = argparse.ArgumentParser(description="批量验证 Omni Realtime 工具调用稳定性。")
    parser.add_argument("--audio", type=Path, default=Path("testdata/audio-sample/帮我找一下手机在哪里.wav"))
    parser.add_argument("--out-dir", type=Path, default=Path("runs/omni-tool-call-stability"))
    parser.add_argument("--model", default="qwen3.5-omni-plus-realtime")
    parser.add_argument("--voice", default="Tina")
    parser.add_argument("--url", default="wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
    parser.add_argument("--input-transcription-model", default="paraformer-realtime-v2")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--chunk-ms", type=int, default=20)
    parser.add_argument("--sleep-ms", type=int, default=20)
    parser.add_argument("--wait-seconds", type=float, default=12.0)
    parser.add_argument("--vad-tail-seconds", type=float, default=1.0)
    parser.add_argument("--session-update-timeout", type=float, default=5.0)
    parser.add_argument("--auto-tool-result", action="store_true", help="收到工具调用后自动回填假结果并继续观察后续响应")
    parser.add_argument(
        "--tool-result-delay-seconds",
        type=float,
        default=0.0,
        help="工具调用完成后延迟多久再回填工具结果，用于观察工具执行窗口内是否乱播",
    )
    parser.add_argument("--fail-on-missing-tool", action="store_true", help="只要存在未调用工具的轮次就返回非 0")
    parser.add_argument(
        "--schema",
        action="append",
        choices=["flat", "nested", "both"],
        default=None,
        help="工具 schema 形态，可重复传入；both 会展开为 flat+nested",
    )
    parser.add_argument(
        "--instructions-mode",
        action="append",
        choices=["weak", "strict", "pre_notice", "pre_notice_guard", "both"],
        default=None,
        help="提示词强度，可重复传入；both 会展开为 weak+strict",
    )
    parser.add_argument(
        "--turn-detection",
        action="append",
        choices=["semantic_vad", "server_vad", "manual", "both"],
        default=None,
        help="turn detection 模式；both 会展开为 semantic_vad+manual",
    )
    return parser


def main() -> int:
    """运行批量 Omni 工具调用稳定性探针。"""

    args = _build_arg_parser().parse_args()
    if not os.getenv("DASHSCOPE_API_KEY"):
        print("缺少 DASHSCOPE_API_KEY，无法连接 DashScope Omni Realtime。", file=sys.stderr)
        return 2
    if args.repeats <= 0:
        print("--repeats 必须大于 0。", file=sys.stderr)
        return 2
    if not args.audio.is_file():
        print(f"音频文件不存在：{args.audio}", file=sys.stderr)
        return 2
    args.out_dir.mkdir(parents=True, exist_ok=True)
    audio_bytes, sample_rate = _read_audio_bytes(args.audio)
    schemas = _expand_matrix(args.schema or ["flat"], both_value="both", choices=("flat", "nested"))
    instruction_modes = _expand_matrix(args.instructions_mode or ["strict"], both_value="both", choices=("weak", "strict"))
    turn_detections = _expand_matrix(args.turn_detection or ["semantic_vad"], both_value="both", choices=("semantic_vad", "manual"))
    summaries: list[ProbeRunSummary] = []
    for schema in schemas:
        for instructions_mode in instruction_modes:
            for turn_detection in turn_detections:
                for index in range(args.repeats):
                    run_id = f"{schema}-{instructions_mode}-{turn_detection}-{index + 1:02d}"
                    summary = _run_once(
                        run_id=run_id,
                        args=args,
                        schema=schema,
                        instructions_mode=instructions_mode,
                        turn_detection=turn_detection,
                        audio_bytes=audio_bytes,
                        sample_rate=sample_rate,
                    )
                    summaries.append(summary)
    aggregate = _aggregate(summaries)
    result = {
        "config": {
            "audio": str(args.audio),
            "model": args.model,
            "voice": args.voice,
            "repeats": args.repeats,
            "schemas": schemas,
            "instructions_modes": instruction_modes,
            "turn_detections": turn_detections,
            "auto_tool_result": args.auto_tool_result,
        },
        "aggregate": aggregate,
        "runs": [asdict(item) | {"has_tool_call": item.has_tool_call} for item in summaries],
    }
    summary_path = args.out_dir / "summary.json"
    summary_path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=_json_default), encoding="utf-8")
    print(json.dumps(aggregate, ensure_ascii=False, indent=2))
    print(f"汇总已写入：{summary_path}")
    if args.fail_on_missing_tool and aggregate["missing_tool_runs"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
