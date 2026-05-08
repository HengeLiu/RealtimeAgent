"""Text Server 对话状态机。

该状态机只服务 Text Server。Omni Server 不等待完整 ASR 文本，也不使用这里的
规则做视觉或误触发主裁决。
"""

from __future__ import annotations

from dataclasses import dataclass


CONVERSATION_STOP_PHRASES = (
    "结束对话",
    "停止对话",
    "退出对话",
    "关闭对话",
    "安静",
    "别说了",
    "不要说了",
    "先别说",
    "静音",
)

FILLER_TEXTS = {
    "嗯",
    "啊",
    "哦",
    "噢",
    "呃",
    "额",
    "喂",
    "好",
    "好的",
    "没事",
    "没有",
}


@dataclass(slots=True)
class TextDialogDecision:
    """Text Server 文本状态机裁决结果。"""

    intent: str
    reason: str
    close_continuous_dialog: bool = False


class TextDialogStateMachine:
    """处理 Text Server 的确定性文本控制规则。"""

    def normalize(self, text: str) -> str:
        return "".join(
            char
            for char in text.strip()
            if char not in " \t\r\n，。！？!?、,.；;：:（）()【】[]“”\"'‘’"
        )

    def is_stop_command(self, text: str) -> bool:
        normalized = self.normalize(text)
        if not normalized or len(normalized) > 12:
            return False
        return any(phrase in normalized for phrase in CONVERSATION_STOP_PHRASES)

    def is_assistant_echo(self, *, text: str, recent_assistant_texts: list[str]) -> bool:
        normalized = self.normalize(text)
        if len(normalized) < 2:
            return False
        for assistant_text in reversed(recent_assistant_texts[-4:]):
            if normalized in self.normalize(assistant_text):
                return True
        return False

    def decide(
        self,
        *,
        transcript: str,
        start_trigger: str,
        recent_assistant_texts: list[str],
    ) -> TextDialogDecision:
        """根据 ASR 文本裁决 Text Server turn。"""

        normalized = self.normalize(transcript)
        if self.is_stop_command(transcript):
            return TextDialogDecision(
                intent="stop_conversation",
                reason="conversation_stop_command",
                close_continuous_dialog=True,
            )
        if not normalized:
            return TextDialogDecision(intent="ignore", reason="empty_transcript", close_continuous_dialog=True)
        if normalized in FILLER_TEXTS:
            return TextDialogDecision(intent="ignore", reason="filler_transcript", close_continuous_dialog=True)
        if self.is_assistant_echo(text=transcript, recent_assistant_texts=recent_assistant_texts):
            return TextDialogDecision(intent="ignore", reason="assistant_echo", close_continuous_dialog=True)
        if start_trigger == "continuous_vad" and len(normalized) == 1:
            return TextDialogDecision(intent="ignore", reason="short_continuous_vad", close_continuous_dialog=True)
        return TextDialogDecision(intent="voice_query", reason="default_voice")
