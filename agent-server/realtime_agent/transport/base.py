from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol

from realtime_agent.protocol import Event, StreamChunk


class ControlTransportABC(Protocol):
    """控制通道传输抽象。

    主要功能：接收设备注册、心跳、命令回执等控制事件，并向设备发送控制事件。
    该抽象不解析模型语义，不判断用户是否说话，也不直接调用 Agent。
    """

    def receive_event(self, event: Event) -> None:
        """接收一个端侧控制事件。"""

    def send_event(self, event: Event) -> None:
        """向端侧发送一个控制事件。"""


class StreamTransportABC(Protocol):
    """数据流传输抽象。

    主要功能：接收上行二进制 stream chunk，发送下行二进制 stream chunk，并管理
    stream 生命周期。该抽象不做 ASR、VAD、TTS 或模型请求构造。
    """

    def receive_chunk(self, chunk: StreamChunk) -> None:
        """接收一个上行 stream chunk。"""

    def open_stream(self, *, session: DeviceSession, stream_type: str, direction: str) -> str:
        """打开一条 stream 并返回 stream_id。"""

    def send_chunk(self, chunk: StreamChunk) -> None:
        """发送一个下行 stream chunk。"""

    def close_stream(self, *, stream_id: str, reason: str) -> None:
        """关闭一条 stream。"""

    def cancel_stream(self, *, stream_id: str, reason: str) -> None:
        """取消一条 stream。"""


@dataclass(frozen=True, slots=True)
class DeviceSession:
    """设备会话抽象。

    主要功能：绑定用户、设备、会话、当前 stream 和设备能力，为输入层和输出层
    提供稳定设备上下文。
    """

    user_id: str
    device_id: str
    session_id: str
    active_streams: Mapping[str, str] = field(default_factory=dict)
    capabilities: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
