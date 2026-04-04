"""阿里百炼对话模型封装。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Iterable, List

from openai import OpenAI


@dataclass
class DashscopeChatService:
    """使用 DashScope 兼容模式的流式对话服务。"""

    api_key: str | None = None
    model: str = "qwen-plus"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.getenv("DASHSCOPE_API_KEY")

    def stream_reply(self, messages: List[Dict[str, str]]) -> Iterable[str]:
        """流式生成回复文本。"""

        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用百炼对话模型。")
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        response = client.chat.completions.create(
            model=self.model,
            messages=messages,
            stream=True,
        )
        for chunk in response:
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                yield delta
