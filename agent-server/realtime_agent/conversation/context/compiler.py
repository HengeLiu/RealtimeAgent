from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from realtime_agent.conversation.context.models import ModelContext, estimate_tokens, normalize_history_message, normalize_tool_call
from realtime_agent.conversation.context.policy import ContextPolicy, _drop_orphan_tool_messages
from realtime_agent.conversation.context.registry import PromptRegistry
from realtime_agent.conversation.context.sources import make_source


@dataclass(frozen=True)
class ContextCompileRequest:
    """上下文编译请求。

    主要功能：把 AgentCore 当前轮需要的 provider、model、用户、历史服务、工具网关
    和当前输入统一交给 ContextCompiler。
    """

    mode: Literal["vision", "omni"]
    provider: str
    model: str
    user_id: str
    session_id: str
    base_instructions: str = ""
    current_input: dict[str, Any] = field(default_factory=dict)
    include_tools: bool = True
    include_realtime_tool_rules: bool = False
    reason: str = "agent_turn"
    memory_service: Any = None
    control_service: Any = None
    tool_gateway: Any = None
    max_context_messages: int = 30


class ContextCompiler:
    """Agent Core 统一上下文编译器。

    主要功能：把 prompt、memory、history、tools、modal input 编译成 ModelContext。
    第一版保留现有运行语义，只把拼接过程集中并记录来源。
    """

    def __init__(self, *, registry: PromptRegistry | None = None, policy: ContextPolicy | None = None) -> None:
        self.registry = registry or PromptRegistry()
        self.policy = policy or ContextPolicy()

    def compile(self, request: ContextCompileRequest) -> ModelContext:
        """编译当前模型请求上下文。

        参数：`request` 为本轮上下文编译请求。
        返回值：ModelContext。
        异常情况：底层 memory/history 读取失败时记录 warning 并继续。
        """

        warnings: list[dict[str, Any]] = []
        truncations: list[dict[str, Any]] = []
        sources = []
        prompt_name = "omni_system" if request.mode == "omni" else "vision_system"
        instructions = _resolve_base_instructions(self.registry, prompt_name, request.base_instructions)
        sources.append(
            make_source(
                source_id=f"prompt:{prompt_name}",
                source_kind="prompt",
                source_name=prompt_name,
                content=instructions,
                priority=10,
            )
        )
        if request.include_realtime_tool_rules:
            rules = _resolve_prompt(self.registry, "omni_tool_call_rules")
            if rules and rules not in instructions:
                instructions = f"{instructions}\n\n{rules}" if instructions else rules
                sources.append(
                    make_source(
                        source_id="prompt:omni_tool_call_rules",
                        source_kind="prompt",
                        source_name="omni_tool_call_rules",
                        content=rules,
                        priority=20,
                    )
                )

        # --- 长期记忆：按 token_budget_memory 截断 ---
        memory_fragment = self._load_memory_fragment(request=request, warnings=warnings)
        if memory_fragment:
            memory_tokens = estimate_tokens(memory_fragment)
            if memory_tokens > self.policy.token_budget_memory:
                memory_fragment = _truncate_text(memory_fragment, self.policy.token_budget_memory)
                truncations.append({"source": "long_term_memory", "reason": "token_budget_memory", "tokens_before": memory_tokens, "tokens_after": estimate_tokens(memory_fragment)})
            instructions = f"{instructions}\n\n{memory_fragment}" if instructions else memory_fragment
            sources.append(
                make_source(
                    source_id=f"memory:{request.user_id}:long_term",
                    source_kind="memory",
                    source_name="long_term_memory",
                    content=memory_fragment,
                    priority=40,
                )
            )

        # --- 历史摘要：按 token_budget_memory 共享预算截断 ---
        summary_fragment = self._load_summary_fragment(request=request, warnings=warnings)
        if summary_fragment:
            summary_tokens = estimate_tokens(summary_fragment)
            remaining_memory_budget = max(0, self.policy.token_budget_memory - estimate_tokens(memory_fragment))
            if summary_tokens > remaining_memory_budget:
                summary_fragment = _truncate_text(summary_fragment, remaining_memory_budget)
                truncations.append({"source": "history_summary", "reason": "token_budget_memory", "tokens_before": summary_tokens, "tokens_after": estimate_tokens(summary_fragment)})
            instructions = f"{instructions}\n\n{summary_fragment}" if instructions else summary_fragment
            sources.append(
                make_source(
                    source_id=f"message_summary:{request.user_id}:{request.session_id}",
                    source_kind="message",
                    source_name="history_summary",
                    content=summary_fragment,
                    priority=50,
                )
            )

        # --- instructions 整体预算检查 ---
        instructions_tokens = estimate_tokens(instructions)
        if instructions_tokens > self.policy.token_budget_instructions:
            instructions = _truncate_text(instructions, self.policy.token_budget_instructions)
            truncations.append({"source": "instructions", "reason": "token_budget_instructions", "tokens_before": instructions_tokens, "tokens_after": estimate_tokens(instructions)})

        active_messages = self._load_active_messages(request=request, warnings=warnings)
        # --- messages 预算裁剪 ---
        messages_tokens = sum(estimate_tokens(msg) for msg in active_messages)
        if messages_tokens > self.policy.token_budget_messages:
            active_messages, dropped = _trim_messages_by_token_budget(active_messages, self.policy.token_budget_messages)
            if dropped > 0:
                truncations.append({"source": "active_messages", "reason": "token_budget_messages", "messages_dropped": dropped, "tokens_after": sum(estimate_tokens(msg) for msg in active_messages)})

        messages = self._build_messages(request=request, active_messages=active_messages)
        if active_messages:
            sources.append(
                make_source(
                    source_id=f"messages:{request.user_id}:{request.session_id}:active",
                    source_kind="message",
                    source_name="active_messages",
                    content=active_messages,
                    priority=60,
                    metadata={"message_count": len(active_messages)},
                )
            )
        current_source = self._current_input_source(request)
        if current_source is not None:
            sources.append(current_source)

        tools = self._build_tools(request=request)
        # --- tools 预算裁剪 ---
        if request.include_tools and tools:
            tools_tokens = estimate_tokens(tools)
            if tools_tokens > self.policy.token_budget_tools:
                tools, dropped = _trim_tools_by_token_budget(tools, self.policy.token_budget_tools)
                if dropped > 0:
                    truncations.append({"source": "tool_schema", "reason": "token_budget_tools", "tools_dropped": dropped, "tokens_after": estimate_tokens(tools)})
            sources.append(
                make_source(
                    source_id="tools:provider_schema",
                    source_kind="tool",
                    source_name="tool_schema",
                    content=tools,
                    priority=70,
                    metadata={"tool_count": len(tools), "tool_names": [_tool_schema_name(item) for item in tools]},
                )
            )

        modal_inputs = []
        if request.mode == "omni":
            modal_inputs.append(
                {
                    "type": "input_audio_stream",
                    "stream_type": "sensor.mic",
                    "note": "Realtime 底层持续发送 PCM 音频；这里是等价请求视图。",
                }
            )

        token_estimate = sum(source.token_estimate or 0 for source in sources if source.included)
        metadata = {
            "reason": request.reason,
            "token_estimate": token_estimate,
            "token_budget_total": self.policy.token_budget_total,
            "active_history_message_count": len(active_messages),
        }
        if token_estimate > self.policy.token_budget_total:
            warnings.append(
                {
                    "code": "context_token_budget_exceeded",
                    "message": "上下文估算 token 超过总预算（各分区裁剪后仍超限）。",
                    "token_estimate": token_estimate,
                    "token_budget_total": self.policy.token_budget_total,
                }
            )
        return ModelContext(
            mode=request.mode,
            provider=request.provider,
            model=request.model,
            instructions=instructions,
            messages=messages,
            tools=tools,
            modal_inputs=modal_inputs,
            context_sources=sources,
            warnings=warnings,
            truncations=truncations,
            metadata=metadata,
        )

    def _load_memory_fragment(self, *, request: ContextCompileRequest, warnings: list[dict[str, Any]]) -> str:
        memory = request.memory_service
        if memory is None or not getattr(memory, "enabled", False):
            return ""
        try:
            return str(memory.build_prompt_fragment(user_id=request.user_id) or "").strip()
        except Exception as exc:  # noqa: BLE001 - 记忆读取失败不应阻断当前回复
            warnings.append({"code": "memory_fragment_failed", "message": str(exc)})
            return ""

    def _load_summary_fragment(self, *, request: ContextCompileRequest, warnings: list[dict[str, Any]]) -> str:
        control = request.control_service
        if control is None:
            return ""
        try:
            return str(control.load_message_summary_fragment(user_id=request.user_id, session_id=request.session_id) or "").strip()
        except Exception as exc:  # noqa: BLE001 - 摘要读取失败降级为空
            warnings.append({"code": "message_summary_failed", "message": str(exc)})
            return ""

    def _load_active_messages(self, *, request: ContextCompileRequest, warnings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        control = request.control_service
        if control is None:
            return []
        try:
            records = control.load_messages(
                user_id=request.user_id,
                session_id=request.session_id,
                limit=max(1, int(request.max_context_messages or self.policy.max_messages)),
            )
        except Exception as exc:  # noqa: BLE001 - 历史读取失败降级为无历史
            warnings.append({"code": "active_messages_failed", "message": str(exc)})
            return []
        messages = [normalize_history_message(record) for record in records]
        return self.policy.trim_messages([message for message in messages if message is not None], max_messages=request.max_context_messages)

    def _build_messages(self, *, request: ContextCompileRequest, active_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = list(active_messages)
        if request.mode == "vision":
            transcript = str(request.current_input.get("text") or request.current_input.get("transcript") or "").strip()
            if transcript:
                current = {"role": "user", "content": transcript}
                if not messages or messages[-1] != current:
                    messages.append(current)
            return self.policy.trim_messages(messages, max_messages=request.max_context_messages)
        return [
            *messages,
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio_stream",
                        "stream_type": "sensor.mic",
                        "note": "Realtime 底层持续发送 PCM 音频；这里是等价请求视图。",
                    }
                ],
            },
        ]

    def _current_input_source(self, request: ContextCompileRequest):
        if not request.current_input:
            return None
        source_kind = "modal" if request.mode == "omni" else "message"
        source_name = "input_audio_stream" if request.mode == "omni" else "current_input"
        return make_source(
            source_id=f"current_input:{request.session_id}",
            source_kind=source_kind,
            source_name=source_name,
            content=request.current_input,
            priority=30,
        )

    def _build_tools(self, *, request: ContextCompileRequest) -> list[dict[str, Any]]:
        if not request.include_tools or request.tool_gateway is None:
            return []
        schemas = list(request.tool_gateway.provider_schemas())
        if request.mode != "omni":
            return schemas
        tools: list[dict[str, Any]] = []
        for schema in schemas:
            if _tool_schema_name(schema) in self.policy.inline_vision_tools:
                continue
            function = schema.get("function") if isinstance(schema, dict) else None
            if isinstance(function, dict):
                tools.append(
                    {
                        "type": "function",
                        "name": function.get("name"),
                        "description": function.get("description", ""),
                        "parameters": function.get("parameters") or {"type": "object", "properties": {}},
                    }
                )
            elif isinstance(schema, dict):
                tools.append(schema)
        return tools


def record_context_events(*, recorder: Any, session_id: str, context: ModelContext) -> None:
    """把上下文编译摘要写入 agent-events。

    主要功能：补齐 `context.compile.*` 和 `context.source.*` 可观测事件。
    参数：`recorder` 为 RunRecorder，`session_id` 为会话编号，`context` 为编译结果。
    返回值：无。
    异常情况：recorder 缺失对应方法时静默跳过，方便单元测试替身使用。
    """

    if recorder is None or not hasattr(recorder, "record_agent_event"):
        return
    recorder.record_agent_event(
        session_id,
        {
            "event": "context.compile.completed",
            "mode": context.mode,
            "provider": context.provider,
            "model": context.model,
            "source_count": len(context.context_sources),
            "tool_count": len(context.tools),
            "warning_count": len(context.warnings),
            "token_estimate": context.metadata.get("token_estimate"),
        },
    )
    for source in context.context_sources:
        event_name = "context.source.added" if source.included else "context.source.skipped"
        recorder.record_agent_event(session_id, {"event": event_name, **source.to_record(include_content=False)})
    for notification in context.notifications:
        recorder.record_agent_event(session_id, {"event": "context.notification.recorded", **notification})


def _resolve_base_instructions(registry: PromptRegistry, prompt_name: str, inline: str) -> str:
    text = str(inline or "").strip()
    if text:
        return text
    return _resolve_prompt(registry, prompt_name)


def _resolve_prompt(registry: PromptRegistry, prompt_name: str) -> str:
    asset = registry.maybe_get(prompt_name)
    return asset.content if asset is not None else ""


def _truncate_text(text: str, token_budget: int) -> str:
    """按 token 预算截断文本，保留前缀并附加截断标记。

    参数：`text` 为原始文本；`token_budget` 为目标 token 数。
    返回值：截断后的文本。
    异常情况：无。
    """


    if estimate_tokens(text) <= token_budget:
        return text
    # 粗略按字符数截断：token_budget * 4 是保守的字符上限（ASCII 场景最多 4 字符/token）
    char_budget = max(100, token_budget * 2)  # 混合场景取 2 字符/token 作为中间值
    truncated = text[:char_budget]
    # 往前找到一个合理的断句点
    for sep in ("\n\n", "\n", "。", "；", ".", ";", " "):
        idx = truncated.rfind(sep)
        if idx > char_budget // 2:
            truncated = truncated[: idx + len(sep)]
            break
    return truncated + "\n…（已截断）"


def _trim_messages_by_token_budget(messages: list[dict[str, Any]], token_budget: int) -> tuple[list[dict[str, Any]], int]:
    """按 token 预算从旧到新裁剪消息列表。

    参数：`messages` 为原始消息列表；`token_budget` 为目标 token 数。
    返回值：(保留的消息列表, 被丢弃的消息数)。
    异常情况：无。
    """


    if not messages:
        return messages, 0
    total = sum(estimate_tokens(msg) for msg in messages)
    if total <= token_budget:
        return messages, 0
    # 从最旧的消息开始丢弃，保留 tool result 与其 tool_call 的配对
    remaining = total
    drop_count = 0
    for i, msg in enumerate(messages):
        msg_tokens = estimate_tokens(msg)
        if remaining - msg_tokens <= token_budget:
            # 丢弃这条后就达标了，但要检查是否是孤立的 tool result
            break
        remaining -= msg_tokens
        drop_count = i + 1
    # 确保不会从 tool_calls 中间截断：如果 drop 位置落在 tool result 上，回退
    while drop_count > 0 and drop_count < len(messages):
        msg = messages[drop_count]
        if msg.get("role") == "tool":
            drop_count -= 1
        else:
            break
    if drop_count <= 0:
        return messages, 0
    trimmed = _drop_orphan_tool_messages(list(messages[drop_count:]))
    return trimmed, drop_count


def _trim_tools_by_token_budget(tools: list[dict[str, Any]], token_budget: int) -> tuple[list[dict[str, Any]], int]:
    """按 token 预算从末尾裁剪工具列表。

    参数：`tools` 为原始工具列表；`token_budget` 为目标 token 数。
    返回值：(保留的工具列表, 被丢弃的工具数)。
    异常情况：无。
    """


    if not tools:
        return tools, 0
    total = estimate_tokens(tools)
    if total <= token_budget:
        return tools, 0
    # 逐步从末尾移除工具直到满足预算
    remaining = total
    for i in range(len(tools) - 1, -1, -1):
        tool_tokens = estimate_tokens(tools[i])
        remaining -= tool_tokens
        if remaining <= token_budget:
            return tools[:i], len(tools) - i
    return [], len(tools)


def _tool_schema_name(schema: dict[str, Any]) -> str:
    function = schema.get("function") if isinstance(schema, dict) else None
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(schema.get("name") or "") if isinstance(schema, dict) else ""
