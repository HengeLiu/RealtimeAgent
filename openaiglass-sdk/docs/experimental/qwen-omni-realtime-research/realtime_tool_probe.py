"""Qwen-Omni-Realtime 工具调用离线调研脚本。

主要功能：
1. 构造符合官方 Realtime API 的 `session.update` 工具定义。
2. 读取仓库中的真实 wav 样例，统计可用于上行音频的分片信息。
3. 模拟模型返回 `response.function_call_arguments.done` 后的本地工具执行。
4. 生成 `conversation.item.create` 和 `response.create` 两个客户端事件。

主要属性：
1. `TOOL_DEFINITIONS`：OpenAI 兼容格式的工具定义，可直接放入 Realtime session。
2. `SAMPLE_AUDIO`：本仓库已有的真实语音样例路径。
3. `ARTIFACT_PATH`：脚本生成的调研结果 JSON。
"""

from __future__ import annotations

import argparse
import json
import math
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


RESEARCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = RESEARCH_DIR.parents[3]
SAMPLE_AUDIO = REPO_ROOT / "openaiglass-sdk/testdata/audio-sample/wav/帮我查一下今天的天气.wav"
ARTIFACT_PATH = RESEARCH_DIR / "artifacts/probe_result.json"

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "查询指定城市的天气，适合回答用户询问天气的问题。",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "城市、区县或当前位置描述，例如北京市、杭州市、上海徐汇区。",
                    }
                },
                "required": ["location"],
            },
        },
    }
]


@dataclass(slots=True)
class AudioSummary:
    """真实音频样例摘要。

    主要功能：
    1. 保存 wav 文件格式、时长和分片数量。
    2. 帮助判断样例是否满足 Realtime `pcm` 输入要求。

    主要属性：
    1. `sample_rate_hz`：采样率。
    2. `channels`：声道数。
    3. `sample_width_bytes`：单个采样点字节数。
    4. `duration_ms`：音频时长。
    5. `chunk_count`：按指定分片大小切分后的分片数量。
    """

    path: str
    sample_rate_hz: int
    channels: int
    sample_width_bytes: int
    frame_count: int
    duration_ms: int
    chunk_bytes: int
    chunk_count: int


def inspect_wav(path: Path, *, chunk_bytes: int) -> AudioSummary:
    """读取 wav 样例并计算 Realtime 上行分片信息。

    主要逻辑：
    1. 使用标准库 `wave` 读取音频头，避免引入额外依赖。
    2. 计算音频时长和按 `chunk_bytes` 切分后的分片数。
    3. 返回结构化摘要，供调研报告和 probe 结果引用。

    参数：
    1. `path`：wav 文件路径。
    2. `chunk_bytes`：每次发送给 Realtime 的 PCM 字节数。

    返回值：
    1. `AudioSummary`，包含格式、时长和分片统计。

    异常情况：
    1. 文件不存在或 wav 头非法时，由 `wave.open` 抛出异常。
    """

    with wave.open(str(path), "rb") as reader:
        frame_count = reader.getnframes()
        sample_rate_hz = reader.getframerate()
        channels = reader.getnchannels()
        sample_width_bytes = reader.getsampwidth()
        total_audio_bytes = frame_count * channels * sample_width_bytes
    duration_ms = int(round(frame_count / sample_rate_hz * 1000))
    chunk_count = int(math.ceil(total_audio_bytes / chunk_bytes))
    return AudioSummary(
        path=str(path),
        sample_rate_hz=sample_rate_hz,
        channels=channels,
        sample_width_bytes=sample_width_bytes,
        frame_count=frame_count,
        duration_ms=duration_ms,
        chunk_bytes=chunk_bytes,
        chunk_count=chunk_count,
    )


def build_session_update(*, turn_detection: str | None) -> dict[str, Any]:
    """构造 Realtime `session.update` 客户端事件。

    主要逻辑：
    1. 使用 `tools` 字段声明工具清单。
    2. `turn_detection=None` 时表示 Manual 模式。
    3. `turn_detection=server_vad|semantic_vad` 时表示 VAD 模式。

    参数：
    1. `turn_detection`：VAD 类型或 `None`。

    返回值：
    1. 可直接发送给 Realtime WebSocket 的事件字典。

    异常情况：
    1. 传入非官方支持的 VAD 类型时抛出 `ValueError`。
    """

    if turn_detection not in {None, "server_vad", "semantic_vad"}:
        raise ValueError(f"不支持的 turn_detection: {turn_detection}")
    session: dict[str, Any] = {
        "modalities": ["text", "audio"],
        "voice": "Tina",
        "input_audio_format": "pcm",
        "output_audio_format": "pcm",
        "instructions": "你是盲人眼镜助手，需要在必要时调用工具，再用简短语音回答用户。",
        "tools": TOOL_DEFINITIONS,
        "temperature": 0.7,
    }
    if turn_detection is None:
        session["turn_detection"] = None
    else:
        session["turn_detection"] = {
            "type": turn_detection,
            "threshold": 0.5,
            "prefix_padding_ms": 300,
            "silence_duration_ms": 800,
        }
    return {
        "event_id": "event_research_session_update",
        "type": "session.update",
        "session": session,
    }


def get_current_weather(arguments: dict[str, Any]) -> str:
    """模拟本地天气工具。

    主要逻辑：
    1. 从模型给出的 JSON 入参里读取 `location`。
    2. 返回稳定的文本结果，便于离线验证工具结果回传格式。

    参数：
    1. `arguments`：模型生成的工具入参。

    返回值：
    1. 天气查询结果字符串。

    异常情况：
    1. 缺少 `location` 时抛出 `ValueError`，模拟真实工具入参校验失败。
    """

    location = str(arguments.get("location") or "").strip()
    if not location:
        raise ValueError("get_current_weather 缺少 location 参数")
    return f"{location}今天多云转晴，气温 18 到 25 摄氏度，东南风 2 级。"


def handle_function_call_done(event: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """处理 Realtime 工具调用完成事件。

    主要逻辑：
    1. 读取 `response.function_call_arguments.done` 中的工具名、call_id 和完整 arguments。
    2. 调用本地工具函数。
    3. 生成 `conversation.item.create` 回传工具结果。
    4. 生成 `response.create` 触发模型最终语音回复。

    参数：
    1. `event`：服务端返回的工具调用完成事件。

    返回值：
    1. 二元组：工具结果回传事件、最终响应创建事件。

    异常情况：
    1. 工具名未知、arguments 不是 JSON 或工具执行失败时抛出异常。
    """

    if event.get("type") != "response.function_call_arguments.done":
        raise ValueError("事件类型必须是 response.function_call_arguments.done")
    tool_name = str(event.get("name") or "")
    call_id = str(event.get("call_id") or "")
    arguments = json.loads(str(event.get("arguments") or "{}"))
    if tool_name != "get_current_weather":
        raise ValueError(f"未知工具: {tool_name}")
    output = get_current_weather(arguments)
    tool_output_event = {
        "event_id": "event_research_tool_output",
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": output,
        },
    }
    response_create_event = {
        "event_id": "event_research_response_after_tool",
        "type": "response.create",
    }
    return tool_output_event, response_create_event


def run_probe(audio_path: Path, *, chunk_bytes: int) -> dict[str, Any]:
    """执行离线调研 probe。

    主要逻辑：
    1. 读取真实音频样例摘要。
    2. 构造 semantic_vad 和 Manual 两种会话配置。
    3. 模拟官方工具调用事件并生成回传事件。
    4. 输出完整 JSON，便于人工审计和后续改造成单元测试。

    参数：
    1. `audio_path`：真实 wav 样例路径。
    2. `chunk_bytes`：Realtime 音频分片字节数。

    返回值：
    1. 调研 probe 的结构化结果。
    """

    audio_summary = inspect_wav(audio_path, chunk_bytes=chunk_bytes)
    function_call_done = {
        "type": "response.function_call_arguments.done",
        "response_id": "resp_research_weather",
        "item_id": "item_research_weather",
        "output_index": 0,
        "name": "get_current_weather",
        "call_id": "call_research_weather",
        "arguments": json.dumps({"location": "上海徐汇区"}, ensure_ascii=False),
    }
    tool_output_event, response_create_event = handle_function_call_done(function_call_done)
    return {
        "audio_sample": asdict(audio_summary),
        "session_update_semantic_vad": build_session_update(turn_detection="semantic_vad"),
        "session_update_manual": build_session_update(turn_detection=None),
        "server_function_call_done": function_call_done,
        "client_tool_output_event": tool_output_event,
        "client_response_create_event": response_create_event,
        "notes": [
            "本 probe 不连接真实 DashScope 服务，只验证本地事件格式、工具分发和真实音频样例分片统计。",
            "真实联调时需要把 wav 内容转为裸 PCM 后按 input_audio_buffer.append 持续发送。",
        ],
    }


def main() -> None:
    """命令行入口。

    主要逻辑：
    1. 解析音频样例和分片参数。
    2. 运行离线 probe。
    3. 将结果写入 artifacts/probe_result.json 并打印摘要。
    """

    parser = argparse.ArgumentParser(description="Qwen-Omni-Realtime 工具调用离线调研 probe")
    parser.add_argument("--audio", type=Path, default=SAMPLE_AUDIO, help="用于调研的 wav 样例路径")
    parser.add_argument("--chunk-bytes", type=int, default=3200, help="模拟 Realtime 上行的每片 PCM 字节数")
    parser.add_argument("--output", type=Path, default=ARTIFACT_PATH, help="probe 结果输出路径")
    args = parser.parse_args()

    result = run_probe(args.audio, chunk_bytes=args.chunk_bytes)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "audio_sample": result["audio_sample"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
