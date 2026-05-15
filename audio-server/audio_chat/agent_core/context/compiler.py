from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from audio_chat.agent_core.context.models import ModelContext
from audio_chat.agent_core.context.policy import ContextPolicy
from audio_chat.agent_core.context.registry import PromptRegistry
from audio_chat.agent_core.context.sources import make_source


@dataclass(frozen=True)
class ContextCompileRequest:
    """上下文编译请求。

    主要功能：把 AgentCore 当前轮需要的 provider、model、用户、历史服务、工具网关
    和当前输入统一交给 ContextCompiler。
    """

    mode: Literal["text", "realtime_audio"]
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
        sources = []
        prompt_name = "realtime_system" if request.mode == "realtime_audio" else "text_system"
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
            rules = _resolve_prompt(self.registry, "realtime_tool_call_rules")
            if rules and rules not in instructions:
                instructions = f"{instructions}\n\n{rules}" if instructions else rules
                sources.append(
                    make_source(
                        source_id="prompt:realtime_tool_call_rules",
                        source_kind="prompt",
                        source_name="realtime_tool_call_rules",
                        content=rules,
                        priority=20,
                    )
                )

        memory_fragment = self._load_memory_fragment(request=request, warnings=warnings)
        if memory_fragment:
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

        summary_fragment = self._load_summary_fragment(request=request, warnings=warnings)
        if summary_fragment:
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

        active_messages = self._load_active_messages(request=request, warnings=warnings)
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
        if request.include_tools:
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
        if request.mode == "realtime_audio":
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
        truncations: list[dict[str, Any]] = []
        if token_estimate > self.policy.token_budget_total:
            warnings.append(
                {
                    "code": "context_token_budget_exceeded",
                    "message": "上下文估算 token 超过预算，第一版仅记录不自动裁剪。",
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
        messages = [_normalize_history_message(record) for record in records]
        return self.policy.trim_messages([message for message in messages if message is not None], max_messages=request.max_context_messages)

    def _build_messages(self, *, request: ContextCompileRequest, active_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        messages = list(active_messages)
        if request.mode == "text":
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
        source_kind = "modal" if request.mode == "realtime_audio" else "message"
        source_name = "input_audio_stream" if request.mode == "realtime_audio" else "current_input"
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
        if request.mode != "realtime_audio":
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


def _normalize_history_message(record: dict[str, Any]) -> dict[str, Any] | None:
    role = str(record.get("role") or "").strip()
    if role not in {"user", "assistant"}:
        return None
    content = record.get("content")
    text = " ".join(content.strip().split()) if isinstance(content, str) else ""
    if not text:
        return None
    return {"role": role, "content": text}


def _tool_schema_name(schema: dict[str, Any]) -> str:
    function = schema.get("function") if isinstance(schema, dict) else None
    if isinstance(function, dict):
        return str(function.get("name") or "")
    return str(schema.get("name") or "") if isinstance(schema, dict) else ""
