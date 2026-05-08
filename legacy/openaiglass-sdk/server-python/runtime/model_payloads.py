"""模型返回和音频输入载荷解析工具。"""

from __future__ import annotations

import base64
from typing import Any


def extract_text_delta(content: Any) -> str:
    """从增量 content 字段提取文本。"""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "".join(parts)
    return ""


def extract_message_text(completion: Any) -> str:
    """从非流式模型返回结果中提取文本。

    参数：
    1. `completion`：OpenAI SDK 返回的完整响应对象。

    返回值：
    1. 提取到的文本；若没有文本则返回空字符串。
    """

    choices = getattr(completion, "choices", None)
    if not isinstance(choices, list) or not choices:
        return ""
    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", None)
    return extract_text_delta(content)


def build_audio_data_url(input_wav: bytes) -> str:
    """把 WAV 字节转成 `data:` URL。

    参数：
    1. `input_wav`：完整 WAV 字节。

    返回值：
    1. `data:audio/wav;base64,...` 格式字符串。
    """

    return "data:audio/wav;base64," + base64.b64encode(input_wav).decode("utf-8")


def read_attr_or_key(value: Any, name: str) -> Any:
    """从对象属性或字典键中读取字段。

    参数：
    1. `value`：待读取对象，可以是普通对象、字典或 `None`。
    2. `name`：字段名。

    返回值：
    1. 读取到的字段值；若不存在则返回 `None`。
    """

    if value is None:
        return None
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
