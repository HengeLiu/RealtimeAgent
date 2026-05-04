"""文本语音链路到 Agent Core 的适配器。"""

from __future__ import annotations

from typing import Any, Callable

from agent_core import AgentTurn, DerivedArtifact, MediaAssetRef
from agent_core.context import generate_id
from runtime.voice_state import SegmentBuffer


class TextAgentAdapter:
    """构造文本语音链路进入 Agent Core 所需的 Turn。

    主要功能：
    1. 保存旁路 ASR 或文本输入的转写产物。
    2. 把语音段音频和转写文本封装为 `AgentTurn`。
    3. 让 `VoiceRuntime` 不再直接关心 Agent Core 数据模型的组装细节。

    主要方法：
    1. `build_voice_text_turn`：根据当前语音段、文本和音频产物构造 Agent Turn。

    主要属性：
    1. `store_artifact`：由运行时注入的产物落盘函数。
    """

    def __init__(
        self,
        *,
        store_artifact: Callable[[str, str, str, dict[str, Any]], str],
    ) -> None:
        """初始化文本 Agent 适配器。

        主要逻辑：
        1. 保存运行时注入的产物落盘函数。

        参数：
        1. `store_artifact`：负责把结构化运行产物写入 session 目录。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化阶段不访问文件系统，不主动抛出业务异常。
        """

        self._store_artifact = store_artifact

    def build_voice_text_turn(
        self,
        *,
        session_id: str,
        device_id: str,
        segment: SegmentBuffer,
        voice_input_mode: str,
        user_text: str,
        input_path: str,
        input_wav: bytes,
    ) -> tuple[AgentTurn, str]:
        """构造文本语音链路的 Agent Turn。

        主要逻辑：
        1. 先把本轮转写结果写成 `transcript` 运行产物。
        2. 再把原始音频文件注册为音频资产。
        3. 最后生成包含音频资产和转写产物的 `AgentTurn`。

        参数：
        1. `session_id/device_id`：当前设备会话标识。
        2. `segment`：当前语音段缓冲，提供 stream、segment 和时长信息。
        3. `voice_input_mode`：当前输入模式，例如 `asr_text`。
        4. `user_text`：准备进入 Agent Core 的用户文本。
        5. `input_path/input_wav`：已落盘的输入音频路径和字节。

        返回值：
        1. `(turn, transcript_path)`：Agent Turn 和转写产物路径。

        异常情况：
        1. 产物落盘失败时透出底层异常，由 `VoiceRuntime` 统一处理。
        """

        transcript_path = self._store_artifact(
            session_id,
            "transcript",
            f"{segment.segment_id}.json",
            {
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
                "transcript": user_text,
                "voice_input_mode": voice_input_mode,
            },
        )
        turn = AgentTurn(
            turn_id=generate_id("turn"),
            session_id=session_id,
            device_id=device_id,
            source=f"voice_{voice_input_mode}",
            input_text=user_text,
            asset_refs=[
                MediaAssetRef(
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
            ],
            derived_artifacts=[
                DerivedArtifact(
                    artifact_id=generate_id("artifact"),
                    session_id=session_id,
                    artifact_type="voice_transcript",
                    storage_uri=transcript_path,
                    text=user_text,
                    meta={
                        "segment_id": segment.segment_id,
                        "stream_id": segment.stream_id,
                        "voice_input_mode": voice_input_mode,
                    },
                )
            ],
            meta={
                "segment_id": segment.segment_id,
                "stream_id": segment.stream_id,
            },
        )
        return turn, transcript_path
