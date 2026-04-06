"""阿里百炼对话模型封装。"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Dict, Iterable, List

import httpx


@dataclass
class DashscopeChatService:
    """使用 DashScope 兼容模式的流式对话服务。"""

    api_key: str | None = None
    model: str = "qwen-plus"
    base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    timeout_sec: float = 60.0

    def __post_init__(self) -> None:
        if self.api_key is None:
            self.api_key = os.getenv("DASHSCOPE_API_KEY")

    def stream_reply(self, messages: List[Dict[str, str]]) -> Iterable[str]:
        """流式生成回复文本。

        主要逻辑：
        - 直接调用 DashScope 兼容接口的 `chat/completions`
        - 使用 SSE 流按行读取返回结果
        - 逐段解析 `delta.content` 并向上游持续产出

        参数：
        - messages：对话消息列表

        返回值：
        - 一个逐段产出模型文本的迭代器

        异常情况：
        - 未配置 API Key 时抛出 `RuntimeError`
        - HTTP 请求失败时透传 `httpx` 异常
        """

        if not self.api_key:
            raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用百炼对话模型。")
        request_payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
        }
        request_headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.timeout_sec) as client:
            with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                headers=request_headers,
                json=request_payload,
            ) as response:
                response.raise_for_status()
                for raw_line in response.iter_lines():
                    if not raw_line:
                        continue
                    line = raw_line.strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    data = json.loads(payload)
                    for choice in data.get("choices", []):
                        delta = choice.get("delta", {}).get("content")
                        if delta:
                            yield str(delta)
