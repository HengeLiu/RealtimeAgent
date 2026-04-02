"""Omni 客户端运行时支持代码。"""

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class OmniStreamPiece:
    """统一的 Omni 流式分片对象。"""

    text_delta: Optional[str] = None
    audio_b64: Optional[str] = None


def build_chat_messages(content_list: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """构造统一的聊天消息结构。"""

    return [{"role": "user", "content": content_list}]


def build_chat_request(
    content_list: List[Dict[str, Any]],
    voice: str = "Cherry",
    audio_format: str = "wav",
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """构造 Omni 聊天请求体。"""

    return {
        "model": model or os.getenv("QWEN_MODEL", "qwen-omni-turbo"),
        "messages": build_chat_messages(content_list),
        "modalities": ["text", "audio"],
        "audio": {
            "voice": voice,
            "format": audio_format,
        },
        "stream": True,
        "stream_options": {"include_usage": True},
    }


class OmniClientFactory:
    """Omni 客户端工厂。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        """初始化工厂。"""

        self.api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.base_url = base_url or os.getenv(
            "DASHSCOPE_COMPAT_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )

    def create(self) -> Any:
        """创建真实客户端。"""

        from openai import OpenAI

        if not self.api_key:
            raise RuntimeError("未设置 DASHSCOPE_API_KEY")
        return OpenAI(api_key=self.api_key, base_url=self.base_url)
