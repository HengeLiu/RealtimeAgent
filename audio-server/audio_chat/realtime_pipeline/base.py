from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from audio_chat.protocol import StreamChunk


@dataclass(frozen=True)
class StreamRef:
    """Realtime pipeline 使用的 stream 引用。

    主要功能：把 `AudioChatApp` 已经完成绑定的 stream 信息传给 pipeline。
    主要属性：`user_id/session_id/stream_id/stream_type` 定位连接，`format`
    保存已注册的音频格式快照。
    """

    user_id: str
    session_id: str
    stream_id: str
    stream_type: str
    format: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PipelineEvent:
    """Realtime pipeline 对外输出的统一事件。

    主要功能：屏蔽 Text / Omni provider 原始事件差异，让外部调用方只处理稳定事件。
    主要属性：`event` 是统一事件名，`payload` 保存事件细节。
    """

    event: str
    user_id: str = ""
    session_id: str = ""
    stream_id: str = ""
    payload: dict = field(default_factory=dict)


class AudioChatRealtimePipeline(Protocol):
    """实时音频对话 pipeline 公共接口。

    主要功能：定义 `AudioChatApp` 和 Text / Omni pipeline 之间的稳定调用边界。
    主要方法：打开会话、绑定上下行连接、写入音频、处理下行水位和关闭会话。
    """

    def open_session(self, user_id: str, session_id: str) -> PipelineEvent:
        """打开一次连续对话 session。"""

    def attach_upstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定上行麦克风 stream。"""

    def attach_downstream(self, stream_ref: StreamRef) -> PipelineEvent:
        """绑定下行扬声器 stream。"""

    def append_input_audio(self, chunk: StreamChunk) -> list[PipelineEvent]:
        """追加一片上行麦克风音频。"""

    def pause_downstream(self, user_id: str, session_id: str) -> PipelineEvent:
        """暂停下行音频发送。"""

    def resume_downstream(self, user_id: str, session_id: str) -> PipelineEvent:
        """恢复下行音频发送。"""

    def notify_output_finished(self, stream_id: str) -> PipelineEvent:
        """通知端侧已完成当前 output stream 播放。"""

    def detach_upstream(self, stream_ref: StreamRef, *, reason: str) -> PipelineEvent:
        """解绑上行麦克风 stream。"""

    def prepare_close(self, user_id: str, session_id: str, *, reason: str) -> PipelineEvent:
        """准备关闭连续对话。"""

    def close_session(self, user_id: str, *, reason: str) -> PipelineEvent:
        """关闭连续对话 session。"""

