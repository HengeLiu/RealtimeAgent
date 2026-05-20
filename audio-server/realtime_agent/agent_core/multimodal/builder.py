from __future__ import annotations

from typing import Any


def build_image_block(url: str) -> dict[str, Any]:
    """构造 Chat Completions 兼容图片 content block。"""

    return {"type": "image_url", "image_url": {"url": url}}


def build_video_block(url: str) -> dict[str, Any]:
    """构造 Chat Completions 兼容视频 content block。

    说明：不同 OpenAI-compatible provider 对 video block 支持程度不同。调用方需要
    通过配置和预检决定是否启用。
    """

    return {"type": "video_url", "video_url": {"url": url}}


def build_tool_asset_followup_message(*, text: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    """把工具资产 content blocks 包装成 follow-up user message。"""

    return {"role": "user", "content": [{"type": "text", "text": text}, *blocks]}
