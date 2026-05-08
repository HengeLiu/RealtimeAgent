"""语音会话模型消息构造器。"""

from __future__ import annotations

from runtime.voice_state import MessageEntry, VoiceSessionController


class VoiceMessageBuilder:
    """把语音会话短期上下文转换为模型消息。

    主要功能：
    1. 固定注入系统提示词。
    2. 读取最近若干条短期上下文。
    3. 把当前轮用户文本追加到模型消息末尾。

    主要方法：
    1. `build_model_messages`：构造可提交给文本模型的消息列表。
    2. `build_history_message`：把单条短期历史转换成模型消息。

    主要属性：
    1. `system_prompt`：当前语音模型使用的系统提示词。
    2. `history_limit`：最多回放的短期历史条数。
    """

    def __init__(self, *, system_prompt: str, history_limit: int = 6) -> None:
        """初始化模型消息构造器。

        主要逻辑：
        1. 保存系统提示词和历史条数上限。

        参数：
        1. `system_prompt`：要注入的系统提示词。
        2. `history_limit`：最多使用的短期历史条数。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化不访问外部系统，不主动抛出业务异常。
        """

        self._system_prompt = system_prompt
        self._history_limit = max(history_limit, 0)

    def build_model_messages(self, controller: VoiceSessionController, user_text: str) -> list[dict[str, str]]:
        """组装模型消息列表。

        主要逻辑：
        1. 固定注入系统提示词。
        2. 回放最近若干轮短期文本上下文。
        3. 把当前轮用户输入追加到末尾。

        参数：
        1. `controller`：当前设备语音会话控制器。
        2. `user_text`：当前轮用户语音经 ASR 转写后的文本。

        返回值：
        1. 可直接提交给文本模型的 `messages`。

        异常情况：
        1. 本方法只读取内存对象，不主动抛出业务异常。
        """

        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": self._system_prompt,
            }
        ]
        history = controller.message_context[-self._history_limit :] if self._history_limit else []
        for entry in history:
            built_message = self.build_history_message(entry)
            if built_message is not None:
                messages.append(built_message)
        messages.append(
            {
                "role": "user",
                "content": user_text,
            }
        )
        return messages

    def build_history_message(self, entry: MessageEntry) -> dict[str, str] | None:
        """把单条历史消息转换为模型可读格式。

        主要逻辑：
        1. 当前短期历史只回放文本内容。
        2. 对无有效内容的消息返回 `None`。

        参数：
        1. `entry`：消息上下文条目。

        返回值：
        1. 可直接放进 `messages` 的字典；若无有效内容则返回 `None`。

        异常情况：
        1. 本方法不访问外部系统，不主动抛出业务异常。
        """

        if entry.text:
            return {"role": entry.role, "content": entry.text}
        return None
