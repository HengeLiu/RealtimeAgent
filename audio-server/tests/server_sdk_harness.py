from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from audio_chat import AudioChatApp
from audio_chat.agent_core.providers import TranscriptEvent
from audio_chat.protocol import Event, StreamChunk


@dataclass
class RecordingEndpoint:
    """Server SDK 系统级测试端点。

    主要功能：模拟一个真实设备连接，记录 server 通过控制通道和 stream 通道下发的内容。
    主要属性：`events` 保存控制事件，`chunks` 保存二进制 stream chunk。
    """

    user_id: str
    device_id: str
    events: list[Event] = field(default_factory=list)
    chunks: list[object] = field(default_factory=list)
    closed_reasons: list[str] = field(default_factory=list)

    def push_event(self, event: Event) -> None:
        """记录 server 下发的控制事件。"""

        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        """记录 server 下发的 stream chunk。"""

        self.chunks.append(chunk)

    def close(self, *, reason: str) -> None:
        """记录连接关闭原因。"""

        self.closed_reasons.append(reason)

    def event_names(self) -> list[str]:
        """返回已收到的控制事件名列表。"""

        return [event.event_name for event in self.events]


class ScriptedAsrProvider:
    """脚本化 ASR provider。

    主要功能：在测试中把麦克风 stream chunk 转成固定 transcript，避免依赖真实 ASR。
    主要属性：`transcript` 是最终转写文本，`chunks` 记录收到的协议 chunk。
    """

    provider_name = "test-scripted-asr"
    model = "test-scripted-asr-model"

    def __init__(self, transcript: str) -> None:
        self.transcript = transcript
        self.chunks: list[StreamChunk] = []
        self.cancelled = False

    def append_audio(self, chunk: StreamChunk) -> list[TranscriptEvent]:
        """收到 final chunk 时返回固定最终转写。

        参数：`chunk` 为 Server SDK 从协议 stream 通道消费到的麦克风分片。
        返回值：非 final 返回空列表，final 返回一个 `TranscriptEvent`。
        异常情况：无。
        """

        self.chunks.append(chunk)
        if not chunk.final:
            return []
        return [TranscriptEvent(text=self.transcript, final=True)]

    def cancel(self) -> None:
        """记录取消请求。"""

        self.cancelled = True


class ScriptedTextModel:
    """脚本化文本模型 provider。

    主要功能：按预设 delta 返回助手回复，并记录 Server SDK 编译后的模型输入。
    主要属性：`calls` 保存每轮 `messages/tools`，用于断言上下文和工具面。
    """

    provider_name = "test-scripted-text"
    model = "test-scripted-text-model"

    def __init__(self, deltas: Iterable[str]) -> None:
        self.deltas = list(deltas)
        self.calls: list[dict[str, Any]] = []
        self.cancelled = False

    def stream_messages(self, *, messages: list[dict], tools: list[dict]):
        """记录模型输入并返回预设文本 delta。"""

        self.calls.append({"messages": list(messages), "tools": list(tools)})
        for delta in self.deltas:
            if self.cancelled:
                return
            yield delta

    def stream_text(self, transcript: str):
        """兼容旧 TextModelAdapter 接口。"""

        yield from self.stream_messages(messages=[{"role": "user", "content": transcript}], tools=[])

    def cancel(self) -> None:
        """记录取消请求。"""

        self.cancelled = True


def register_audio_device(app: AudioChatApp, *, user_id: str, device_id: str) -> RecordingEndpoint:
    """注册一台具备系统麦克风和扬声器链路的测试设备。

    主要逻辑：设备公开 `supports` 仍为空结构，麦克风和扬声器通过 properties 声明为
    系统音频主链路，符合当前协议边界。
    参数：`app` 为待测 Server SDK 应用，`user_id/device_id` 为设备身份。
    返回值：记录型端点。
    异常情况：注册失败时由断言暴露。
    """

    endpoint = RecordingEndpoint(user_id=user_id, device_id=device_id)
    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=user_id,
            producer_id=device_id,
            payload={
                "device_id": device_id,
                "device_name": device_id,
                "client_type": "server-sdk-contract-test",
                "sdk_version": "test",
                "auth": {"mode": "disabled"},
                "supports": {"sensors": [], "actuators": []},
                "properties": {
                    "audio_chat.audio_input": "sensor.mic",
                    "audio_chat.audio_output": "actuator.speaker",
                },
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"
    return endpoint


def install_text_turn_providers(
    app: AudioChatApp,
    *,
    stream_id: str,
    transcript: str,
    response_deltas: Iterable[str],
) -> tuple[ScriptedAsrProvider, ScriptedTextModel]:
    """把脚本化 ASR 和文本模型安装到 Text Agent Core。

    主要逻辑：仅在测试内替换 provider 对象，真实 SDK 构造和配置路径不受影响。
    参数：`stream_id` 绑定本轮麦克风输入流，`transcript` 和 `response_deltas` 描述模型脚本。
    返回值：安装后的 ASR provider 和文本模型 provider。
    异常情况：当前 app 不是 Text 链路或缺少 provider 容器时抛出 AttributeError。
    """

    core = getattr(app.agent_core, "core", app.agent_core)
    asr_provider = ScriptedAsrProvider(transcript)
    text_model = ScriptedTextModel(response_deltas)
    core.asr_pipeline._providers[stream_id] = asr_provider
    core.text_model = text_model
    return asr_provider, text_model
