from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal


SourceKind = Literal["prompt", "memory", "message", "tool", "modal", "runtime"]
ContextMode = Literal["vision", "omni"]


@dataclass(frozen=True)
class PromptAsset:
    """PromptRegistry 返回的提示词资产。

    主要功能：把 registry 中的 `name/file/description` 与 Markdown 正文绑定到一起。
    主要属性：`name` 是开发者可搜索的唯一名称，`content` 是模型可见正文。
    """

    name: str
    file: str
    description: str
    content: str

    def to_record(self) -> dict[str, Any]:
        """转换为可写入运行产物的提示词摘要。"""

        return {
            "name": self.name,
            "file": self.file,
            "description": self.description,
            "content_chars": len(self.content),
        }


@dataclass(frozen=True)
class ContextSource:
    """一段模型可见上下文的来源记录。

    主要功能：解释某段内容从哪里来、为什么会进入本轮模型请求，以及预算处理状态。
    主要属性：`source_kind` 是粗粒度来源，例如 prompt、memory、message、tool。
    """

    source_id: str
    source_kind: SourceKind
    source_name: str
    content: Any
    token_estimate: int | None = None
    priority: int = 100
    included: bool = True
    reason: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_record(self, *, include_content: bool = False) -> dict[str, Any]:
        """转换为可写入 `model-request.json` 的来源摘要。

        参数：`include_content` 控制是否落完整内容；默认只写长度和预览，避免重复膨胀。
        返回值：JSON 友好的 dict。
        异常情况：不可序列化内容会退化为字符串预览。
        """

        record: dict[str, Any] = {
            "source_id": self.source_id,
            "source_kind": self.source_kind,
            "source_name": self.source_name,
            "token_estimate": self.token_estimate,
            "priority": self.priority,
            "included": self.included,
        }
        if self.reason:
            record["reason"] = self.reason
        if self.metadata:
            record.update(self.metadata)
        if include_content:
            record["content"] = self.content
        else:
            record["content_preview"] = _preview(self.content)
            record["content_chars"] = len(_stringify(self.content))
        return record


@dataclass(frozen=True)
class ModelContext:
    """ContextCompiler 输出的统一模型上下文。

    主要功能：让 Vision / Omni Agent Core 使用同一种结构读取 instructions、
    messages、tools、modal_inputs 和 context_sources。
    """

    mode: ContextMode
    provider: str
    model: str
    instructions: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]]
    modal_inputs: list[dict[str, Any]]
    context_sources: list[ContextSource]
    warnings: list[dict[str, Any]] = field(default_factory=list)
    notifications: list[dict[str, Any]] = field(default_factory=list)
    truncations: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def prompt_records(self) -> list[dict[str, Any]]:
        """返回本轮使用的 prompt 摘要。"""

        records: list[dict[str, Any]] = []
        for source in self.context_sources:
            if source.source_kind != "prompt" or not source.included:
                continue
            records.append(
                {
                    "name": source.source_name,
                    "source_id": source.source_id,
                    "token_estimate": source.token_estimate,
                }
            )
        return records

    def source_records(self, *, include_content: bool = False) -> list[dict[str, Any]]:
        """返回所有上下文来源摘要。"""

        return [source.to_record(include_content=include_content) for source in self.context_sources]


def estimate_tokens(value: Any) -> int:
    """用轻量字符数估算 token 数。

    主要逻辑：第一版不引入 provider tokenizer，按中英文混合场景用字符数近似估算。
    参数：`value` 是任意上下文内容。
    返回值：至少为 1 的估算 token 数。
    异常情况：无。
    """

    text = _stringify(value)
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _preview(value: Any, *, limit: int = 240) -> str:
    text = _stringify(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return str(value)


def normalize_history_message(record: dict[str, Any]) -> dict[str, Any] | None:
    """把落盘消息转换为模型可消费的历史上下文。

    主要逻辑：回灌 user/assistant 文本，并保留成对工具历史中的 assistant
    tool_calls 与 tool result，避免后续轮次只看到普通助手文本。
    参数：`record` 为 messages.jsonl 中的一行。
    返回值：模型消息或 None。
    异常情况：字段缺失或内容为空时返回 None。
    """

    role = str(record.get("role") or "").strip()
    if role == "assistant" and isinstance(record.get("tool_calls"), list):
        tool_calls = [normalize_tool_call(item) for item in record.get("tool_calls") or [] if isinstance(item, dict)]
        tool_calls = [item for item in tool_calls if item is not None]
        if tool_calls:
            content = record.get("content") if isinstance(record.get("content"), str) else ""
            return {"role": "assistant", "content": content, "tool_calls": tool_calls}
    if role == "tool":
        tool_call_id = str(record.get("tool_call_id") or "").strip()
        if not tool_call_id:
            return None
        content = record.get("content")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False, default=str)
        return {"role": "tool", "tool_call_id": tool_call_id, "content": content}
    if role not in {"user", "assistant"}:
        return None
    content = record.get("content")
    text = " ".join(content.strip().split()) if isinstance(content, str) else ""
    if not text:
        return None
    return {"role": role, "content": text}


def normalize_tool_call(item: dict[str, Any]) -> dict[str, Any] | None:
    """把落盘的 tool_call 记录转换为 OpenAI-compatible 格式。

    主要逻辑：兼容 id/tool_call_id、name/function.name、arguments 为 str 或 dict
    等多种落盘格式，输出统一的 function 调用结构。
    参数：`item` 为 tool_calls 列表中的一个元素。
    返回值：标准化的 tool_call 字典或 None。
    异常情况：缺少 id 或 name 时返回 None。
    """

    call_id = str(item.get("id") or item.get("tool_call_id") or "").strip()
    name = str(item.get("name") or "").strip()
    arguments = item.get("arguments") or {}
    function = item.get("function") if isinstance(item.get("function"), dict) else None
    if function is not None:
        name = name or str(function.get("name") or "").strip()
        arguments = arguments or function.get("arguments") or {}
    if not call_id or not name:
        return None
    if not isinstance(arguments, str):
        try:
            arguments = json.dumps(arguments, ensure_ascii=False, default=str)
        except TypeError:
            arguments = json.dumps({"_raw_arguments": str(arguments)}, ensure_ascii=False)
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }
