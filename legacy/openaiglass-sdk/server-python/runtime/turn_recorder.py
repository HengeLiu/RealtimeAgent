"""语音轮次运行产物记录器。"""

from __future__ import annotations

from typing import Any, Callable

from agent_core import MediaAssetRef
from agent_core.context import generate_id
from runtime.audio_utils import build_wav_bytes
from runtime.voice_state import SegmentBuffer


class VoiceTurnRecorder:
    """记录语音轮次的音频资产和结构化产物。

    主要功能：
    1. 为输入语音段和输出回复音频创建 `MediaAssetRef`。
    2. 统一保存 transcript artifact 和 output WAV asset。
    3. 把最终回复音频挂回 Agent 会话，便于回放和排障。

    主要方法：
    1. `build_input_audio_asset`：构造输入音频资产引用。
    2. `store_transcript_artifact`：保存本轮转写产物。
    3. `store_output_audio`：保存回复音频 WAV。
    4. `attach_assistant_audio`：把回复音频挂到 assistant message。

    主要属性：
    1. `store_artifact/store_asset`：运行时注入的产物落盘函数。
    2. `agent_facade`：可选 Agent 门面，用于挂载 assistant 音频资产。
    """

    def __init__(
        self,
        *,
        store_artifact: Callable[[str, str, str, dict[str, Any]], str],
        store_asset: Callable[[str, str, str, bytes], str],
        agent_facade: Any | None = None,
    ) -> None:
        """初始化语音轮次记录器。

        主要逻辑：
        1. 保存运行时注入的落盘函数和可选 Agent 门面。

        参数：
        1. `store_artifact`：保存 JSON 等结构化产物。
        2. `store_asset`：保存音频、图片等二进制资产。
        3. `agent_facade`：支持 `attach_assistant_asset(...)` 的 Agent 门面。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化阶段不访问文件系统，不主动抛出业务异常。
        """

        self._store_artifact = store_artifact
        self._store_asset = store_asset
        self._agent_facade = agent_facade

    def build_input_audio_asset(
        self,
        *,
        session_id: str,
        segment: SegmentBuffer,
        input_path: str,
        input_wav: bytes,
    ) -> MediaAssetRef:
        """构造输入语音段的音频资产引用。

        主要逻辑：
        1. 根据当前 segment 的时长和 stream_id 生成统一音频资产元数据。

        参数：
        1. `session_id`：当前会话编号。
        2. `segment`：当前语音段。
        3. `input_path/input_wav`：已保存的输入 WAV 路径和字节。

        返回值：
        1. `MediaAssetRef` 音频资产引用。

        异常情况：
        1. 本方法只构造内存对象，不访问外部系统。
        """

        return MediaAssetRef(
            asset_id=generate_id("asset"),
            session_id=session_id,
            asset_type="audio",
            storage_uri=input_path,
            mime_type="audio/wav",
            codec="pcm16le",
            duration_ms=segment.duration_ms(),
            bytes=len(input_wav),
            source_stream_id=segment.stream_id,
        )

    def store_transcript_artifact(
        self,
        *,
        session_id: str,
        segment: SegmentBuffer,
        payload: dict[str, Any],
    ) -> str:
        """保存当前语音段的转写产物。

        主要逻辑：
        1. 使用固定目录 `transcript` 和 segment_id 命名 JSON 文件。
        2. 调用运行时注入的 `store_artifact` 完成落盘。

        参数：
        1. `session_id`：当前会话编号。
        2. `segment`：当前语音段。
        3. `payload`：需要写入 JSON 的结构化内容。

        返回值：
        1. 转写产物路径。

        异常情况：
        1. 落盘失败时透出底层异常，由调用方统一处理。
        """

        return self._store_artifact(session_id, "transcript", f"{segment.segment_id}.json", payload)

    def store_output_audio(
        self,
        *,
        session_id: str,
        stream_id: str,
        output_pcm: bytes,
        sample_rate_hz: int,
        channels: int,
    ) -> str:
        """保存模型回复音频。

        主要逻辑：
        1. 把 PCM16 音频封装成 WAV。
        2. 使用播放 stream_id 命名输出音频文件。

        参数：
        1. `session_id/stream_id`：当前会话和播放流编号。
        2. `output_pcm`：模型或 TTS 已生成的 PCM 字节。
        3. `sample_rate_hz/channels`：输出音频参数。

        返回值：
        1. 输出 WAV 文件路径。

        异常情况：
        1. WAV 封装或资产落盘失败时透出异常。
        """

        return self._store_asset(
            session_id,
            "output",
            f"{stream_id}.wav",
            build_wav_bytes(output_pcm, sample_rate_hz, channels),
        )

    def attach_assistant_audio(
        self,
        *,
        session_id: str,
        assistant_message_id: str | None,
        output_path: str,
        output_pcm: bytes,
        source_stream_id: str,
    ) -> None:
        """把回复音频资产挂到 Agent assistant message。

        主要逻辑：
        1. 没有 assistant message 或未注入 Agent 门面时直接返回。
        2. 构造输出音频资产并调用 Agent 门面挂载。

        参数：
        1. `session_id`：当前会话编号。
        2. `assistant_message_id`：Agent 返回的 assistant 消息编号。
        3. `output_path/output_pcm`：回复音频路径和 PCM 字节。
        4. `source_stream_id`：下行播放流编号。

        返回值：
        1. 无返回值。

        异常情况：
        1. Agent 门面挂载失败时透出异常，由调用方统一处理。
        """

        if not assistant_message_id or self._agent_facade is None:
            return
        self._agent_facade.attach_assistant_asset(
            session_id=session_id,
            assistant_message_id=assistant_message_id,
            asset=MediaAssetRef(
                asset_id=generate_id("asset"),
                session_id=session_id,
                asset_type="audio",
                storage_uri=output_path,
                mime_type="audio/wav",
                codec="pcm16le",
                bytes=len(output_pcm),
                source_stream_id=source_stream_id,
            ),
        )
