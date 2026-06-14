from __future__ import annotations

from realtime_agent.conversation.types import AgentOutputDelta
from realtime_agent.output import OutputService
from realtime_agent.protocol import StreamFormat


class AgentOutputRouter:
    """Agent 输出路由器。

    主要功能：接收 `AgentOutputDelta` 并根据输出类型转交给现有 `OutputService`。
    当前实现先覆盖 conversation runtime 已需要的文本、原生音频和取消请求路径；
    后续 provider loop 完全 delta 化后可以继续扩展 output_started/output_finished。
    """

    def __init__(self, *, output_service: OutputService) -> None:
        self.output_service = output_service

    def route(self, delta: AgentOutputDelta) -> None:
        """路由一个 Agent 输出增量。

        主要逻辑：文本交给 TTS 文本输出；原生音频交给直接播放；取消请求转成
        `OutputService.interrupt_user()`。
        参数：`delta` 为 Agent Core 产出的标准输出增量。
        返回值：无。
        异常情况：底层 OutputService 异常向上传播。
        """

        user_id = str(delta.metadata.get("user_id") or "")
        if delta.kind in {"text", "text_delta", "text_final"}:
            text = str(delta.payload) if delta.payload is not None else (delta.text_delta or "")
            if text:
                self.output_service.submit_text(
                    user_id=user_id,
                    session_id=delta.session_id,
                    text=text,
                )
            return
        if delta.kind in {"audio", "audio_chunk"}:
            audio = delta.payload if isinstance(delta.payload, bytes) else delta.audio
            if audio is None:
                return
            sample_rate_hz = delta.sample_rate_hz or int(delta.metadata.get("sample_rate_hz") or 24000)
            self.output_service.submit_audio(
                user_id=user_id,
                session_id=delta.session_id,
                audio=audio,
                format=StreamFormat(codec="pcm16le", sample_rate=sample_rate_hz, channels=1),
                metadata={"output_id": delta.output_id, **dict(delta.metadata)},
            )
            return
        if delta.kind == "control":
            control = delta.payload if isinstance(delta.payload, dict) else {}
            if str(control.get("action") or "") != "cancel_output":
                return
            self.output_service.interrupt_user(
                user_id,
                session_id=delta.session_id,
                reason=str(control.get("reason") or delta.metadata.get("reason") or "agent_output_cancel_requested"),
            )
            return
        if delta.kind == "output_cancel_requested":
            self.output_service.interrupt_user(
                user_id,
                session_id=delta.session_id,
                reason=str(delta.metadata.get("reason") or "agent_output_cancel_requested"),
            )
