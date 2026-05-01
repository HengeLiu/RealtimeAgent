"""agent-core 运行循环实现。"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_core.context.models import AgentSession, AgentTurn, AgentTurnResult
from agent_core.context.session_store import AgentSessionStore
from agent_core.memory import AgentMemoryRuntime
from agent_core.skills import SkillRuntime
from agent_core.tools import AgentToolContext, ToolGateway, ToolRegistry
from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_error, log_info

_IMAGE_FOLLOWUP_TOOL_NAMES: set[str] = set()


@dataclass(slots=True)
class AgentTurnRuntime:
    """单轮 Agent 运行所需的轻量上下文。"""

    tool_context: AgentToolContext
    capability_traces: list
    instructions: str
    active_skill_names: list[str]
    allowed_tool_names: set[str] | None
    memory_prompt_fragment: str
    run_input: list[dict[str, Any]]
    sdk_tools: list
    model_request: dict[str, object]


class AgentTurnRuntimeFactory:
    """负责把会话与工具注册表装配成单轮运行上下文。"""

    def __init__(
        self,
        *,
        settings: ServerSettings,
        session_store: AgentSessionStore,
        tool_registry: ToolRegistry,
        tool_gateway: ToolGateway,
        skill_runtime: SkillRuntime | None,
        memory_runtime: AgentMemoryRuntime | None,
        instruction_builder: Callable[[str | None], str],
        history_builder: Callable[[AgentSession, AgentTurn], list[dict[str, Any]]],
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._tool_gateway = tool_gateway
        self._skill_runtime = skill_runtime
        self._memory_runtime = memory_runtime
        self._instruction_builder = instruction_builder
        self._history_builder = history_builder

    def build(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnRuntime:
        """装配本轮运行态，不触发模型或外部 I/O。"""

        capability_traces = []
        tool_context = AgentToolContext(
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            settings=self._settings,
            session_store=self._session_store,
            device_state_reader=self._tool_registry.get_device_state_reader(),
            device_group_context_factory=self._tool_registry.get_device_group_context_factory(),
            trace_sink=capability_traces.append,
            task_gateway=self._tool_registry.get_task_gateway(),
            camera_gateway=self._tool_registry.get_camera_gateway(),
            utterance_photo_store=self._tool_registry.get_utterance_photo_store(),
            tool_gateway=self._tool_gateway,
            mcp_gateway=self._tool_registry.get_mcp_gateway(),
            memory_runtime=self._memory_runtime,
            turn_meta=dict(turn.meta),
        )
        memory_prompt_fragment = (
            self._memory_runtime.build_prompt_fragment(
                scope_type="device",
                scope_id=turn.device_id,
                query=turn.input_text,
            )
            if self._memory_runtime is not None
            else ""
        )
        instructions = self._instruction_builder(turn.session_id)
        if memory_prompt_fragment:
            instructions = f"{instructions}\n{memory_prompt_fragment}\n"
        allowed_tool_names = (
            self._skill_runtime.allowed_tool_names_for_session(session_id=turn.session_id)
            if self._skill_runtime is not None
            else None
        )
        active_skill_names = (
            self._skill_runtime.get_session_state(turn.session_id).active_skill_names
            if self._skill_runtime is not None
            else []
        )
        run_input = self._history_builder(session, turn)
        sdk_tools = self._tool_registry.list_sdk_tools(allowed_names=allowed_tool_names)
        model_request = {
            "model": self._settings.agent_model_name,
            "instructions": instructions,
            "active_skills": active_skill_names,
            "allowed_tool_names": sorted(allowed_tool_names) if allowed_tool_names is not None else None,
            "memory_prompt_fragment": memory_prompt_fragment,
            "extra_body": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": instructions},
                *OpenAIAgentLoopRunner._sanitize_model_messages(run_input),
            ],
        }
        return AgentTurnRuntime(
            tool_context=tool_context,
            capability_traces=capability_traces,
            instructions=instructions,
            active_skill_names=active_skill_names,
            allowed_tool_names=allowed_tool_names,
            memory_prompt_fragment=memory_prompt_fragment,
            run_input=run_input,
            sdk_tools=sdk_tools,
            model_request=model_request,
        )


class OpenAIAgentsSdkBridge:
    """缓存 OpenAI Agents SDK 入口和可复用 provider。"""

    def __init__(self, *, settings: ServerSettings) -> None:
        self._settings = settings
        self._agent_cls = None
        self._multi_provider_cls = None
        self._run_config_cls = None
        self._runner_cls = None
        self._message_output_item_cls = None
        self._model_settings_cls = None
        self._provider = None
        self._provider_key: tuple[str, str] | None = None
        self._last_import_error: ImportError | None = None

    def preload(self) -> None:
        """预加载 SDK 模块和 provider；失败只记录，真实调用时再返回结构化错误。"""

        try:
            self._ensure_agents_sdk()
            if self._settings.dashscope_api_key.strip():
                self._get_provider()
        except ImportError as exc:
            self._last_import_error = exc

    def is_available(self) -> bool:
        """判断 Agents SDK 依赖是否已经可用。"""

        try:
            self._ensure_agents_sdk()
            return True
        except ImportError as exc:
            self._last_import_error = exc
            return False

    def build_agent(self, *, instructions: str, tools: list):
        """创建本轮 Agent 对象。"""

        self._ensure_agents_sdk()
        return self._agent_cls(
            name="OpenAIGlassesAgent",
            instructions=instructions,
            tools=tools,
            model=self._settings.agent_model_name,
        )

    def build_run_config(self, *, session_id: str):
        """创建本轮 RunConfig，复用已缓存的 provider。"""

        self._ensure_agents_sdk()
        return self._run_config_cls(
            model=self._settings.agent_model_name,
            model_provider=self._get_provider(),
            model_settings=self._model_settings_cls(extra_body={"enable_thinking": False}),
            tracing_disabled=True,
            workflow_name="OpenAI Glasses Agent Loop",
            group_id=session_id,
        )

    def run_sync(self, *args, **kwargs):
        """代理 `Runner.run_sync`。"""

        self._ensure_agents_sdk()
        return self._runner_cls.run_sync(*args, **kwargs)

    def run_streamed(self, *args, **kwargs):
        """代理 `Runner.run_streamed`。"""

        self._ensure_agents_sdk()
        return self._runner_cls.run_streamed(*args, **kwargs)

    @property
    def message_output_item_cls(self):
        """返回 Agents SDK 的 MessageOutputItem 类型。"""

        self._ensure_agents_sdk()
        return self._message_output_item_cls

    @staticmethod
    def create_openai_client(*, api_key: str, base_url: str):
        """创建 OpenAI SDK 客户端。"""

        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 环境缺包时由上层集成验证
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 openai 依赖，无法执行主链路图片解读",
                details={"hint": "请执行 uv sync 或安装 openai 依赖"},
            ) from exc

        return OpenAI(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
        )

    def _ensure_agents_sdk(self) -> None:
        if self._runner_cls is not None:
            return
        try:
            from agents import Agent, MultiProvider, RunConfig, Runner
        except ImportError as exc:
            self._last_import_error = exc
            raise
        try:
            from agents.items import MessageOutputItem
        except ImportError:
            class MessageOutputItem:  # noqa: N801 - 兼容测试替身或旧版 SDK 结构
                pass
        try:
            from agents.model_settings import ModelSettings
        except ImportError:
            class ModelSettings:  # noqa: N801 - 兼容测试替身或旧版 SDK 结构
                def __init__(self, *args, **kwargs) -> None:
                    self.args = args
                    self.kwargs = kwargs

        self._agent_cls = Agent
        self._multi_provider_cls = MultiProvider
        self._run_config_cls = RunConfig
        self._runner_cls = Runner
        self._message_output_item_cls = MessageOutputItem
        self._model_settings_cls = ModelSettings
        self._last_import_error = None

    def _get_provider(self):
        key = (self._settings.dashscope_api_key, self._settings.voice_model_base_url)
        if self._provider is not None and self._provider_key == key:
            return self._provider
        self._ensure_agents_sdk()
        self._provider = self._multi_provider_cls(
            openai_api_key=self._settings.dashscope_api_key,
            openai_base_url=self._settings.voice_model_base_url,
            openai_use_responses=False,
        )
        self._provider_key = key
        return self._provider


class AgentStreamDiagnostics:
    """记录 Agent 流式链路的关键时间点。

    主要功能：
    1. 只记录首个关键事件，避免流式增量日志刷屏。
    2. 区分模型原始流事件与 Agents SDK 语义事件。
    3. 帮助判断延迟发生在模型思考、工具调用增量，还是 SDK 高层事件转换阶段。
    """

    def __init__(self, *, logger, turn: AgentTurn) -> None:
        """创建诊断器。

        参数：
        1. `logger`：当前 agent-core 日志器。
        2. `turn`：本轮对话对象，用于补充设备、会话与消息编号。
        """

        self._logger = logger
        self._turn = turn
        self._started_at = time.perf_counter()
        self._seen: set[str] = set()

    def observe(self, event: object, *, stage: str) -> None:
        """观察一个流式事件并按需打印关键耗时。

        参数：
        1. `event`：`Runner.run_streamed(...).stream_events()` 产出的事件。
        2. `stage`：当前链路阶段名称，便于在日志中区分首轮工具选择等阶段。

        异常情况：
        1. 本方法不主动抛出异常，无法识别的事件会被忽略。
        """

        event_type = OpenAIAgentLoopRunner._read_event_value(event, "type")
        if event_type == "raw_response_event":
            self._observe_raw_event(event, stage=stage)
            return
        if event_type == "run_item_stream_event":
            self._observe_run_item_event(event, stage=stage)

    def _observe_raw_event(self, event: object, *, stage: str) -> None:
        data = OpenAIAgentLoopRunner._read_event_value(event, "data")
        raw_type = str(OpenAIAgentLoopRunner._read_event_value(data, "type") or "")
        self._log_once(
            "first_raw_event",
            "Agent 流式诊断: 收到首个模型原始事件",
            stage=stage,
            raw_type=raw_type,
        )
        if raw_type in {"response.reasoning_text.delta", "response.reasoning_summary_text.delta"}:
            delta = OpenAIAgentLoopRunner._read_event_value(data, "delta")
            self._log_once(
                "first_reasoning_delta",
                "Agent 流式诊断: 收到首个 reasoning 增量",
                stage=stage,
                raw_type=raw_type,
                delta_chars=len(delta) if isinstance(delta, str) else None,
            )
            return
        if raw_type == "response.output_text.delta":
            delta = OpenAIAgentLoopRunner._read_event_value(data, "delta")
            self._log_once(
                "first_text_delta",
                "Agent 流式诊断: 收到首个文本增量",
                stage=stage,
                raw_type=raw_type,
                delta_chars=len(delta) if isinstance(delta, str) else None,
            )
            return
        if raw_type == "response.output_item.added":
            item = OpenAIAgentLoopRunner._read_event_value(data, "item")
            item_type = str(OpenAIAgentLoopRunner._read_event_value(item, "type") or "")
            if item_type == "function_call":
                self._log_once(
                    "first_tool_call_added",
                    "Agent 流式诊断: 收到首个工具调用原始 added 事件",
                    stage=stage,
                    raw_type=raw_type,
                    tool_name=OpenAIAgentLoopRunner._read_event_value(item, "name"),
                    call_id=OpenAIAgentLoopRunner._read_event_value(item, "call_id"),
                )
            return
        if raw_type == "response.function_call_arguments.delta":
            delta = OpenAIAgentLoopRunner._read_event_value(data, "delta")
            self._log_once(
                "first_tool_call_arguments_delta",
                "Agent 流式诊断: 收到首个工具参数增量",
                stage=stage,
                raw_type=raw_type,
                delta_chars=len(delta) if isinstance(delta, str) else None,
            )
            return
        if raw_type == "response.output_item.done":
            item = OpenAIAgentLoopRunner._read_event_value(data, "item")
            item_type = str(OpenAIAgentLoopRunner._read_event_value(item, "type") or "")
            if item_type == "function_call":
                arguments = OpenAIAgentLoopRunner._read_event_value(item, "arguments")
                self._log_once(
                    "first_tool_call_done",
                    "Agent 流式诊断: 收到首个工具调用原始 done 事件",
                    stage=stage,
                    raw_type=raw_type,
                    tool_name=OpenAIAgentLoopRunner._read_event_value(item, "name"),
                    call_id=OpenAIAgentLoopRunner._read_event_value(item, "call_id"),
                    arguments_chars=len(arguments) if isinstance(arguments, str) else None,
                )
            return
        if raw_type == "response.completed":
            self._log_once(
                "response_completed",
                "Agent 流式诊断: 模型原始响应完成",
                stage=stage,
                raw_type=raw_type,
            )

    def _observe_run_item_event(self, event: object, *, stage: str) -> None:
        event_name = str(OpenAIAgentLoopRunner._read_event_value(event, "name") or "")
        if event_name != "tool_called":
            return
        item = OpenAIAgentLoopRunner._read_event_value(event, "item")
        raw_item = OpenAIAgentLoopRunner._read_event_value(item, "raw_item")
        self._log_once(
            "semantic_tool_called",
            "Agent 流式诊断: 收到 Agents SDK 语义化工具调用事件",
            stage=stage,
            event_name=event_name,
            tool_name=OpenAIAgentLoopRunner._read_event_value(raw_item, "name"),
            call_id=OpenAIAgentLoopRunner._read_event_value(raw_item, "call_id"),
        )

    def _log_once(self, key: str, message: str, **fields: object) -> None:
        if key in self._seen:
            return
        self._seen.add(key)
        log_debug(
            self._logger,
            message,
            LogContext(
                device_id=self._turn.device_id,
                session_id=self._turn.session_id,
                message_id=self._turn.turn_id,
                fields={
                    "latency_ms": int((time.perf_counter() - self._started_at) * 1000),
                    **fields,
                },
            ),
        )


class StreamedAgentTurnObserver:
    """观察 Agents SDK 流式事件，并把事件转换成 SDK 结果。"""

    def __init__(
        self,
        *,
        sdk_bridge: OpenAIAgentsSdkBridge,
        logger,
        wait_for_new_image_asset: Callable[..., Any],
        stream_image_followup_reply: Callable[..., Any],
        collect_image_asset_ids: Callable[..., set[str]],
        extract_agent_stream_text_delta: Callable[[object], str],
        extract_reply_text: Callable[[object], str],
    ) -> None:
        self._sdk_bridge = sdk_bridge
        self._logger = logger
        self._wait_for_new_image_asset = wait_for_new_image_asset
        self._stream_image_followup_reply = stream_image_followup_reply
        self._collect_image_asset_ids = collect_image_asset_ids
        self._extract_agent_stream_text_delta = extract_agent_stream_text_delta
        self._extract_reply_text = extract_reply_text

    async def run(
        self,
        *,
        agent,
        run_input: list[dict[str, Any]],
        run_config,
        tool_context: AgentToolContext,
        capability_traces: list,
        session: AgentSession,
        turn: AgentTurn,
        progress_callback: Callable[[str], None] | None,
        reply_text_delta_callback: Callable[[str], None] | None,
        model_request: dict[str, object],
    ) -> AgentTurnResult:
        """运行流式 Agent，并处理拍照续跑与文本增量。"""

        run_result = self._sdk_bridge.run_streamed(
            agent,
            run_input,
            context=tool_context,
            max_turns=6,
            run_config=run_config,
            conversation_id=turn.session_id,
        )

        capture_call_id: str | None = None
        existing_image_asset_ids = self._collect_image_asset_ids(tool_context=tool_context, session=session)
        reply_text_parts: list[str] = []
        diagnostics = AgentStreamDiagnostics(logger=self._logger, turn=turn)

        event_stream = run_result.stream_events()
        try:
            async for event in event_stream:
                diagnostics.observe(event, stage="agent_first_turn")
                text_delta = self._extract_agent_stream_text_delta(event)
                if text_delta:
                    reply_text_parts.append(text_delta)
                    if reply_text_delta_callback is not None:
                        reply_text_delta_callback(text_delta)
                    continue

                if getattr(event, "type", "") != "run_item_stream_event":
                    continue

                if event.name == "message_output_created" and isinstance(
                    event.item,
                    self._sdk_bridge.message_output_item_cls,
                ):
                    continue

                if event.name == "tool_called":
                    raw_item = getattr(event.item, "raw_item", None)
                    tool_name = getattr(raw_item, "name", "")
                    call_id = getattr(raw_item, "call_id", None)
                    log_debug(
                        self._logger,
                        (
                            "agent-core 工具调用请求: "
                            f"tool_name={tool_name} call_id={call_id} "
                            f"arguments={OpenAIAgentLoopRunner._summarize_for_log(getattr(raw_item, 'arguments', ''))}"
                        ),
                        LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
                    )
                    if tool_name in _IMAGE_FOLLOWUP_TOOL_NAMES:
                        capture_call_id = call_id
                        image_asset = await self._wait_for_new_image_asset(
                            tool_context=tool_context,
                            session=session,
                            excluded_asset_ids=existing_image_asset_ids,
                            timeout_seconds=10.0,
                        )
                        if image_asset is not None:
                            return await self._run_image_followup(
                                run_result=run_result,
                                tool_context=tool_context,
                                turn=turn,
                                image_asset=image_asset,
                                session=session,
                                reply_text_delta_callback=reply_text_delta_callback,
                                capability_traces=capability_traces,
                                model_request=model_request,
                            )
                    continue

                if event.name == "tool_output":
                    raw_item = getattr(event.item, "raw_item", None)
                    log_debug(
                        self._logger,
                        (
                            "agent-core 工具调用结果: "
                            f"call_id={getattr(raw_item, 'call_id', None)} "
                            f"output={OpenAIAgentLoopRunner._summarize_for_log(getattr(raw_item, 'output', event.item))}"
                        ),
                        LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
                    )
                    if capture_call_id is not None and getattr(raw_item, "call_id", None) == capture_call_id:
                        image_asset = await self._wait_for_new_image_asset(
                            tool_context=tool_context,
                            session=session,
                            excluded_asset_ids=existing_image_asset_ids,
                            timeout_seconds=1.0,
                        )
                        return await self._run_image_followup(
                            run_result=run_result,
                            tool_context=tool_context,
                            turn=turn,
                            image_asset=image_asset,
                            session=session,
                            reply_text_delta_callback=reply_text_delta_callback,
                            capability_traces=capability_traces,
                            model_request=model_request,
                        )
                    continue
        finally:
            aclose = getattr(event_stream, "aclose", None)
            if callable(aclose):
                await aclose()

        reply_text = "".join(reply_text_parts).strip() or self._extract_reply_text(run_result.final_output)
        if not reply_text:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "agent-core 返回了空回复",
            )
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            reply_text=reply_text,
            capability_traces=capability_traces,
            meta={"model_request": model_request},
        )

    async def _run_image_followup(
        self,
        *,
        run_result,
        tool_context: AgentToolContext,
        turn: AgentTurn,
        image_asset,
        session: AgentSession,
        reply_text_delta_callback: Callable[[str], None] | None,
        capability_traces: list,
        model_request: dict[str, object],
    ) -> AgentTurnResult:
        run_result.cancel()
        reply_text = await self._stream_image_followup_reply(
            tool_context=tool_context,
            turn=turn,
            image_asset=image_asset,
            history_session=session,
            reply_text_delta_callback=reply_text_delta_callback,
        )
        if not reply_text:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "图片解读主链路返回空回复",
            )
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            reply_text=reply_text,
            capability_traces=capability_traces,
            meta={
                "model_request": model_request,
                "followup_type": "image_vision_main_chain",
            },
        )


class AgentLoopRunner(ABC):
    """Agent Loop 抽象接口。"""

    @abstractmethod
    def run_turn(
        self,
        *,
        session: AgentSession,
        turn: AgentTurn,
        progress_callback: Callable[[str], None] | None = None,
        reply_text_delta_callback: Callable[[str], None] | None = None,
    ) -> AgentTurnResult:
        """执行一轮 AgentTurn。"""


class OpenAIAgentLoopRunner(AgentLoopRunner):
    """基于 OpenAI Agents SDK 的最小运行循环。"""

    def __init__(
        self,
        *,
        settings: ServerSettings,
        session_store: AgentSessionStore,
        tool_registry: ToolRegistry,
        tool_gateway: ToolGateway,
        skill_runtime: SkillRuntime | None = None,
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._tool_gateway = tool_gateway
        self._skill_runtime = skill_runtime or tool_registry.get_skill_runtime()
        self._memory_runtime = tool_registry.get_memory_runtime()
        self._logger = get_logger("server.agent.runner")
        self._turn_runtime_factory = AgentTurnRuntimeFactory(
            settings=settings,
            session_store=session_store,
            tool_registry=tool_registry,
            tool_gateway=tool_gateway,
            skill_runtime=self._skill_runtime,
            memory_runtime=self._memory_runtime,
            instruction_builder=self._build_instructions,
            history_builder=self._build_history_messages,
        )
        self._sdk_bridge = OpenAIAgentsSdkBridge(settings=settings)
        self._stream_observer = StreamedAgentTurnObserver(
            sdk_bridge=self._sdk_bridge,
            logger=self._logger,
            wait_for_new_image_asset=lambda **kwargs: self._wait_for_new_image_asset(**kwargs),
            stream_image_followup_reply=lambda **kwargs: self._stream_image_followup_reply(**kwargs),
            collect_image_asset_ids=self._collect_image_asset_ids,
            extract_agent_stream_text_delta=self._extract_agent_stream_text_delta,
            extract_reply_text=self._extract_reply_text,
        )

    def preload_resources(self) -> None:
        """预热 Agent 运行所需的外部 SDK 入口和 provider。"""

        self._sdk_bridge.preload()

    def run_turn(
        self,
        *,
        session: AgentSession,
        turn: AgentTurn,
        progress_callback: Callable[[str], None] | None = None,
        reply_text_delta_callback: Callable[[str], None] | None = None,
    ) -> AgentTurnResult:
        """执行一轮 AgentTurn。

        主要逻辑：
        1. 默认仍使用 Agents SDK 的标准工具循环。
        2. 当调用方提供进度回调时，切换到流式观察模式。
        3. 当前轮自动照片已在输入装配阶段放入 user message，不再经过照片工具决策。

        参数：
        1. `session`：当前会话对象。
        2. `turn`：当前轮输入对象。
        3. `progress_callback`：中间播报回调，适合在长耗时工具前给用户一句反馈。
        4. `reply_text_delta_callback`：最终回复文本增量回调，便于调用方做流式 TTS。

        返回值：
        1. `AgentTurnResult`。
        """

        runtime = self._turn_runtime_factory.build(session=session, turn=turn)
        runtime.tool_context.progress_callback = progress_callback

        if not self._settings.dashscope_api_key.strip():
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="缺少 DASHSCOPE_API_KEY，无法执行 agent-core 运行循环",
                    traces=runtime.capability_traces,
                    error=build_error(
                        ErrorCode.INVALID_CONFIG,
                        "缺少 DASHSCOPE_API_KEY，无法执行 agent-core 运行循环",
                    ),
                ),
                context=runtime.tool_context,
            )

        if self._turn_has_audio_asset(turn):
            try:
                direct_result = self._run_direct_audio_turn(
                    session=session,
                    turn=turn,
                    runtime=runtime,
                    progress_callback=progress_callback,
                    reply_text_delta_callback=reply_text_delta_callback,
                )
            except Exception as exc:
                log_error(
                    self._logger,
                    f"agent-core 音频原生链路异常: reason={exc!r}",
                    LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
                )
                direct_result = self._build_failure_result(
                    turn=turn,
                    message="agent-core 音频原生链路运行失败",
                    traces=runtime.capability_traces,
                    error=build_error(
                        ErrorCode.INTERNAL_ERROR,
                        "agent-core 音频原生链路运行失败",
                        details={"reason": str(exc)},
                    ),
                )
            return self._attach_capability_outputs(result=direct_result, context=runtime.tool_context)

        if not self._sdk_bridge.is_available():
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="缺少 openai-agents 依赖，无法执行 agent-core 运行循环",
                    traces=runtime.capability_traces,
                    error=build_error(
                        ErrorCode.INVALID_CONFIG,
                        "缺少 openai-agents 依赖，无法执行 agent-core 运行循环",
                        details={"hint": "请执行 uv sync 安装 openai-agents"},
                    ),
                ),
                context=runtime.tool_context,
            )

        streaming_mode = progress_callback is not None or reply_text_delta_callback is not None
        agent = self._sdk_bridge.build_agent(instructions=runtime.instructions, tools=runtime.sdk_tools)
        run_config = self._sdk_bridge.build_run_config(session_id=turn.session_id)
        log_info(
            self._logger,
            (
                "agent-core 即将运行: "
                f"model={self._settings.agent_model_name} mode={'streamed' if streaming_mode else 'sync'} "
                f"message_count={len(runtime.run_input) + 1} tool_count={len(runtime.sdk_tools)} "
                f"timeout_ms={self._settings.voice_model_timeout_ms}"
            ),
            LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
        )

        try:
            if progress_callback is None and reply_text_delta_callback is None:
                run_result = self._run_with_thread_event_loop(
                    lambda: self._sdk_bridge.run_sync(
                        agent,
                        runtime.run_input,
                        context=runtime.tool_context,
                        max_turns=6,
                        run_config=run_config,
                        conversation_id=turn.session_id,
                    )
                )
                reply_text = self._extract_reply_text(run_result.final_output)
                if not reply_text:
                    return self._attach_capability_outputs(
                        result=self._build_failure_result(
                            turn=turn,
                            message="agent-core 返回了空回复",
                            traces=runtime.capability_traces,
                            error=build_error(
                                ErrorCode.INTERNAL_ERROR,
                                "agent-core 返回了空回复",
                            ),
                        ),
                        context=runtime.tool_context,
                    )
                return self._attach_capability_outputs(
                    result=AgentTurnResult(
                        turn_id=turn.turn_id,
                        session_id=turn.session_id,
                        device_id=turn.device_id,
                        reply_text=reply_text or "抱歉，我现在还没法稳定回答这个问题。",
                        capability_traces=runtime.capability_traces,
                        meta={"model_request": runtime.model_request},
                    ),
                    context=runtime.tool_context,
                )

            stream_result = self._run_async_with_thread_event_loop(
                self._run_streamed_turn(
                    agent=agent,
                    run_input=runtime.run_input,
                    run_config=run_config,
                    tool_context=runtime.tool_context,
                    capability_traces=runtime.capability_traces,
                    session=session,
                    turn=turn,
                    progress_callback=progress_callback,
                    reply_text_delta_callback=reply_text_delta_callback,
                    model_request=runtime.model_request,
                ),
                timeout_seconds=max(5.0, self._settings.voice_model_timeout_ms / 1000),
            )
        except Exception as exc:
            log_error(
                self._logger,
                f"agent-core 运行异常: reason={exc!r}",
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="agent-core 运行失败",
                    traces=runtime.capability_traces,
                    error=build_error(
                        ErrorCode.INTERNAL_ERROR,
                        "agent-core 运行失败",
                        details={"reason": str(exc)},
                    ),
                ),
                context=runtime.tool_context,
            )

        return self._attach_capability_outputs(
            result=stream_result,
            context=runtime.tool_context,
        )

    def _run_direct_audio_turn(
        self,
        *,
        session: AgentSession,
        turn: AgentTurn,
        runtime: AgentTurnRuntime,
        progress_callback: Callable[[str], None] | None,
        reply_text_delta_callback: Callable[[str], None] | None,
    ) -> AgentTurnResult:
        """执行音频原生 Omni Agent 轮次。

        主要逻辑：
        1. 直接把当前轮 WAV 音频作为 `input_audio` 发给 Omni 主模型。
        2. 同一请求携带本轮自动照片和模型可见工具 schema。
        3. 如果模型返回工具调用，使用 `ToolGateway` 执行后把结果回填给模型继续推理。

        参数：
        1. `session`：当前会话快照，用于构造历史上下文。
        2. `turn`：当前用户轮次，必须包含音频资产。
        3. `runtime`：Agent-Core 已装配好的工具、记忆和提示词上下文。
        4. `reply_text_delta_callback`：最终文本增量回调；流式响应时会随模型文本分片触发。

        返回值：
        1. 统一的 `AgentTurnResult`。

        异常情况：
        1. 模型调用、工具参数解析或工具执行失败时向上抛出，由 `run_turn` 统一转失败结果。
        """

        client = self._create_sdk_client(
            api_key=self._settings.dashscope_api_key,
            base_url=self._settings.voice_model_base_url,
        )
        messages = self._build_direct_chat_messages(
            session=session,
            turn=turn,
            instructions=runtime.instructions,
        )
        tools = self._build_chat_completion_tools(runtime.allowed_tool_names)
        audio_agent_model = self._settings.voice_model_name
        model_request = {
            "model": audio_agent_model,
            "runner": "direct_chat_audio",
            "active_skills": runtime.active_skill_names,
            "allowed_tool_names": (
                sorted(runtime.allowed_tool_names) if runtime.allowed_tool_names is not None else None
            ),
            "memory_prompt_fragment": runtime.memory_prompt_fragment,
            "extra_body": {"enable_thinking": False},
            "messages": self._sanitize_model_messages(messages),
            "tool_count": len(tools),
        }
        runtime.tool_context.progress_callback = progress_callback
        log_info(
            self._logger,
            (
                "agent-core 音频原生链路即将运行: "
                f"model={audio_agent_model} message_count={len(messages)} "
                f"tool_count={len(tools)} timeout_ms={self._settings.voice_model_timeout_ms}"
            ),
            LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
        )

        for step_index in range(6):
            request_kwargs: dict[str, Any] = {
                "model": audio_agent_model,
                "messages": messages,
                "stream": True,
                "extra_body": {"enable_thinking": False},
                "timeout": self._settings.voice_model_timeout_ms / 1000,
            }
            if tools:
                request_kwargs["tools"] = tools
            completion = client.chat.completions.create(**request_kwargs)
            reply_text, tool_calls = self._consume_direct_chat_stream(
                completion=completion,
                reply_text_delta_callback=reply_text_delta_callback,
            )
            if not tool_calls:
                final_text = reply_text.strip()
                if not final_text:
                    return self._build_failure_result(
                        turn=turn,
                        message="agent-core 返回了空回复",
                        traces=runtime.capability_traces,
                        error=build_error(ErrorCode.INTERNAL_ERROR, "agent-core 返回了空回复"),
                    )
                model_request["messages"] = self._sanitize_model_messages(messages)
                return AgentTurnResult(
                    turn_id=turn.turn_id,
                    session_id=turn.session_id,
                    device_id=turn.device_id,
                    reply_text=final_text,
                    capability_traces=runtime.capability_traces,
                    meta={"model_request": model_request},
                )

            messages.append(self._build_assistant_tool_call_message({"content": reply_text}, tool_calls))
            for tool_call in tool_calls:
                tool_name = str(tool_call["name"])
                arguments_text = str(tool_call.get("arguments") or "{}")
                try:
                    arguments = json.loads(arguments_text)
                except json.JSONDecodeError as exc:
                    raise build_error(
                        ErrorCode.INVALID_MESSAGE,
                        "模型返回的工具参数不是合法 JSON",
                        details={"tool_name": tool_name, "arguments": arguments_text},
                    ) from exc
                log_debug(
                    self._logger,
                    (
                        "agent-core 音频原生链路工具调用请求: "
                        f"step={step_index + 1} tool_name={tool_name} tool_call_id={tool_call['id']} "
                        f"arguments={self._summarize_for_log(arguments)}"
                    ),
                    LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
                )
                result = self._tool_gateway.invoke(
                    name=tool_name,
                    context=runtime.tool_context,
                    arguments=arguments,
                )
                log_debug(
                    self._logger,
                    (
                        "agent-core 音频原生链路工具调用结果: "
                        f"step={step_index + 1} tool_name={tool_name} tool_call_id={tool_call['id']} "
                        f"ok={result.ok} data={self._summarize_for_log(result.data)} "
                        f"assets={len(result.asset_refs)} artifacts={len(result.derived_artifacts)} "
                        f"tasks={len(result.task_refs)}"
                    ),
                    LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(tool_call["id"]),
                        "name": tool_name,
                        "content": json.dumps(result.data, ensure_ascii=False, default=str),
                    }
                )
            log_debug(
                self._logger,
                (
                    "agent-core 音频原生链路完成一轮工具调用: "
                    f"step={step_index + 1} tool_names={[call['name'] for call in tool_calls]}"
                ),
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )

        return self._build_failure_result(
            turn=turn,
            message="agent-core 工具循环超过最大轮数",
            traces=runtime.capability_traces,
            error=build_error(ErrorCode.INTERNAL_ERROR, "agent-core 工具循环超过最大轮数"),
        )

    async def _run_streamed_turn(
        self,
        *,
        agent,
        run_input: list[dict[str, Any]],
        run_config,
        tool_context: AgentToolContext,
        capability_traces: list,
        session: AgentSession,
        turn: AgentTurn,
        progress_callback: Callable[[str], None] | None,
        reply_text_delta_callback: Callable[[str], None] | None,
        model_request: dict[str, object],
    ) -> AgentTurnResult:
        """执行带中间播报能力的流式 AgentTurn。

        主要逻辑：
        1. 先以流式模式运行 Agents SDK，观察工具调用事件。
        2. 当前轮自动照片已作为 user message 图片输入发送给模型。
        3. 若模型直接输出文本，则持续透传给上层流式 TTS。

        返回值：
        1. 完整 `AgentTurnResult`。
        """

        return await self._stream_observer.run(
            agent=agent,
            run_input=run_input,
            run_config=run_config,
            tool_context=tool_context,
            capability_traces=capability_traces,
            session=session,
            turn=turn,
            progress_callback=progress_callback,
            reply_text_delta_callback=reply_text_delta_callback,
            model_request=model_request,
        )

    async def _wait_for_new_image_asset(
        self,
        *,
        tool_context: AgentToolContext,
        session: AgentSession,
        excluded_asset_ids: set[str],
        timeout_seconds: float,
    ):
        """等待工具链路产出本次新图片。

        主要逻辑：
        1. 只接受当前抓拍后新增的图片资产。
        2. 不允许回退到会话里旧的历史图片。
        3. 超时后返回 `None`，由上层决定后续处理。
        """

        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            image_asset = self._resolve_latest_image_asset(
                tool_context=tool_context,
                session=session,
                excluded_asset_ids=excluded_asset_ids,
            )
            if image_asset is not None:
                return image_asset
            await asyncio.sleep(0.01)
        return None

    async def _stream_image_followup_reply(
        self,
        *,
        tool_context: AgentToolContext,
        turn: AgentTurn,
        image_asset: MediaAssetRef | None,
        history_session: AgentSession,
        reply_text_delta_callback: Callable[[str], None] | None,
    ) -> str:
        """在主链路中基于真实图片继续生成最终回复。

        主要逻辑：
        1. 读取最近一次抓拍得到的图片资产。
        2. 使用主链路模型直接查看图片并流式生成最终回答。
        3. 每拿到一段文本增量，就透传给上层做流式 TTS。
        """

        if image_asset is None:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "已完成抓拍，但没有找到可供主链路解读的图片资产",
            )

        client = self._create_sdk_client(
            api_key=self._settings.dashscope_api_key,
            base_url=self._settings.voice_model_base_url,
        )
        messages = self._build_history_messages(session=history_session, turn=turn)
        history_messages = messages[:-1]
        image_data_url = self._build_image_data_url(image_asset.storage_uri, image_asset.mime_type)
        log_debug(
            self._logger,
            (
                "拍照后切换到主链路图片解读: "
                f"asset_id={image_asset.asset_id} mime_type={image_asset.mime_type} "
                f"storage_uri={image_asset.storage_uri}"
            ),
            LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
        )

        image_request_started_at = time.perf_counter()
        completion = client.chat.completions.create(
            model=self._settings.agent_model_name,
            messages=[
                {"role": "system", "content": self._build_image_followup_instructions()},
                *history_messages,
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": turn.input_text.strip(),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url,
                                "detail": "auto",
                            },
                        },
                    ],
                },
            ],
            stream=True,
            extra_body={"enable_thinking": False},
            timeout=self._settings.voice_model_timeout_ms / 1000,
        )
        log_debug(
            self._logger,
            "图片解读流式诊断: 模型流已打开",
            LogContext(
                device_id=turn.device_id,
                session_id=turn.session_id,
                message_id=turn.turn_id,
                fields={
                    "latency_ms": int((time.perf_counter() - image_request_started_at) * 1000),
                    "stage": "image_followup",
                    "model": self._settings.agent_model_name,
                },
            ),
        )

        reply_parts: list[str] = []
        first_text_logged = False
        for chunk in completion:
            text_delta = self._extract_stream_text_delta(chunk)
            if not text_delta:
                continue
            if not first_text_logged:
                first_text_logged = True
                log_debug(
                    self._logger,
                    "图片解读流式诊断: 收到首个文本增量",
                    LogContext(
                        device_id=turn.device_id,
                        session_id=turn.session_id,
                        message_id=turn.turn_id,
                        fields={
                            "latency_ms": int((time.perf_counter() - image_request_started_at) * 1000),
                            "stage": "image_followup",
                            "delta_chars": len(text_delta),
                        },
                    ),
                )
            reply_parts.append(text_delta)
            if reply_text_delta_callback is not None:
                reply_text_delta_callback(text_delta)
        reply_text = "".join(reply_parts).strip()
        log_debug(
            self._logger,
            f"主链路图片解读完成: reply_length={len(reply_text)}",
            LogContext(
                device_id=turn.device_id,
                session_id=turn.session_id,
                message_id=turn.turn_id,
                fields={
                    "latency_ms": int((time.perf_counter() - image_request_started_at) * 1000),
                    "stage": "image_followup",
                },
            ),
        )
        return reply_text

    @staticmethod
    def _resolve_latest_image_asset(
        *,
        tool_context: AgentToolContext,
        session: AgentSession,
        excluded_asset_ids: set[str] | None = None,
    ):
        """从当前上下文里找最近一次抓拍图片。

        主要逻辑：
        1. 优先从本轮新发出的资产中查找。
        2. 若需要，可排除旧资产编号，确保拿到的是新图。
        3. 只有没有排除要求时，才允许回退到会话历史图片。
        """

        excluded = excluded_asset_ids or set()

        for asset in reversed(tool_context.emitted_assets):
            if asset.asset_type == "image" and asset.asset_id not in excluded:
                return asset
        if excluded:
            return None
        for message in reversed(session.messages):
            for asset_id in reversed(message.asset_refs):
                asset = session.assets.get(asset_id)
                if asset is not None and asset.asset_type == "image" and asset.asset_id not in excluded:
                    return asset
        return None

    @staticmethod
    def _collect_image_asset_ids(*, tool_context: AgentToolContext, session: AgentSession) -> set[str]:
        """收集当前会话里已存在的图片资产编号。"""

        image_asset_ids = {
            asset.asset_id
            for asset in tool_context.emitted_assets
            if asset.asset_type == "image"
        }
        image_asset_ids.update(
            asset.asset_id
            for asset in session.assets.values()
            if asset.asset_type == "image"
        )
        return image_asset_ids

    @staticmethod
    def _build_image_data_url(storage_uri: str, mime_type: str) -> str:
        """把本地图片文件转成 `data:` URL。"""

        with open(storage_uri, "rb") as handle:
            payload = handle.read()
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{mime_type or 'image/jpeg'};base64,{encoded}"

    @staticmethod
    def _create_sdk_client(*, api_key: str, base_url: str):
        """创建 OpenAI SDK 客户端。"""

        return OpenAIAgentsSdkBridge.create_openai_client(api_key=api_key, base_url=base_url)

    @staticmethod
    def _extract_stream_text_delta(chunk: object) -> str:
        """从流式 Chat Completions 分片里提取文本增量。"""

        choices = getattr(chunk, "choices", None)
        if not isinstance(choices, list) or not choices:
            return ""
        delta = getattr(choices[0], "delta", None)
        content = getattr(delta, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts)
        return ""

    @classmethod
    def _extract_agent_stream_text_delta(cls, event: object) -> str:
        """从 Agents SDK 流式事件中提取普通文本增量。

        主要逻辑：
        1. 优先处理 `raw_response_event` 中的 `response.output_text.delta`。
        2. 兼容 SDK 事件对象和字典两种结构。
        3. 保留 Chat Completions 风格的 `choices[].delta.content` 兜底。

        参数：
        1. `event`：`Runner.run_streamed(...).stream_events()` 产出的事件。

        返回值：
        1. 本次事件中的文本增量；没有文本时返回空字符串。

        异常情况：
        1. 本函数不主动抛出异常，无法识别的事件会被忽略。
        """

        event_type = cls._read_event_value(event, "type")
        if event_type == "raw_response_event":
            data = cls._read_event_value(event, "data")
            raw_type = str(cls._read_event_value(data, "type") or "")
            if raw_type in {"response.output_text.delta", "response.refusal.delta"} or raw_type.endswith(
                ".output_text.delta"
            ):
                delta = cls._read_event_value(data, "delta")
                return delta if isinstance(delta, str) else ""
            return cls._extract_stream_text_delta(data)

        if event_type == "run_item_stream_event":
            event_name = str(cls._read_event_value(event, "name") or "")
            if event_name not in {"message_output_delta", "message_delta"}:
                return ""
            item = cls._read_event_value(event, "item")
            delta = cls._read_event_value(item, "delta")
            if isinstance(delta, str):
                return delta
            text = cls._read_event_value(item, "text")
            return text if isinstance(text, str) else ""

        return ""

    @staticmethod
    def _read_event_value(source: object, key: str) -> object:
        """读取事件对象或字典中的字段。"""

        if isinstance(source, dict):
            return source.get(key)
        return getattr(source, key, None)

    @staticmethod
    def _attach_capability_outputs(*, result: AgentTurnResult, context: AgentToolContext) -> AgentTurnResult:
        """把能力调用产生的引用对象挂到 turn result。"""

        result.meta.setdefault("asset_refs", list(context.emitted_assets))
        result.meta.setdefault("derived_artifacts", list(context.emitted_artifacts))
        result.meta.setdefault("task_refs", list(context.emitted_tasks))
        return result

    @staticmethod
    def _build_failure_result(
        *,
        turn: AgentTurn,
        message: str,
        traces: list,
        error=None,
    ) -> AgentTurnResult:
        """构造统一失败结果。"""

        meta = {}
        if error is not None and hasattr(error, "to_dict"):
            meta["error"] = error.to_dict()
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            reply_text=f"抱歉，这一轮处理失败了：{message}",
            capability_traces=traces,
            error=meta.get("error"),
            meta=meta,
        )

    @staticmethod
    def _run_with_thread_event_loop(callback):
        """确保当前线程存在可用 event loop 后再执行回调。"""

        previous_loop = None
        created_loop = None

        try:
            previous_loop = asyncio.get_running_loop()
        except RuntimeError:
            previous_loop = None

        if previous_loop is None or previous_loop.is_closed():
            created_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(created_loop)

        try:
            return callback()
        finally:
            if created_loop is not None:
                asyncio.set_event_loop(None)
                created_loop.close()

    @staticmethod
    def _run_async_with_thread_event_loop(awaitable, *, timeout_seconds: float | None = None):
        """确保当前线程存在可用 event loop 后执行协程。"""

        previous_loop = None
        created_loop = None

        try:
            previous_loop = asyncio.get_running_loop()
        except RuntimeError:
            previous_loop = None

        if previous_loop is None or previous_loop.is_closed():
            created_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(created_loop)
            target_loop = created_loop
        else:
            target_loop = previous_loop

        try:
            if timeout_seconds is not None:
                awaitable = asyncio.wait_for(awaitable, timeout=timeout_seconds)
            return target_loop.run_until_complete(awaitable)
        finally:
            if created_loop is not None:
                asyncio.set_event_loop(None)
                created_loop.close()

    @staticmethod
    def _turn_has_audio_asset(turn: AgentTurn) -> bool:
        """判断当前轮是否包含原生音频输入。"""

        return any(asset.asset_type == "audio" for asset in turn.asset_refs)

    def _build_direct_chat_messages(
        self,
        *,
        session: AgentSession,
        turn: AgentTurn,
        instructions: str,
    ) -> list[dict[str, Any]]:
        """构造直接 Chat Completions 音频请求消息。

        主要逻辑：
        1. system 消息沿用 Agent-Core 的统一提示词。
        2. 历史只使用已经落盘的文本轮次，避免重复上传历史音频。
        3. 当前轮同时携带说明文本、音频和本轮照片。
        """

        messages: list[dict[str, Any]] = [{"role": "system", "content": instructions}]
        for message in session.messages:
            if message.meta.get("turn_id") == turn.turn_id:
                continue
            if message.role not in {"user", "assistant"}:
                continue
            text = message.text.strip()
            if not text:
                continue
            messages.append({"role": message.role, "content": text})

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": turn.input_text.strip()
                or "用户发送了一段语音，请直接理解音频内容并执行用户意图。",
            }
        ]
        for asset in turn.asset_refs:
            if asset.asset_type == "audio":
                content.append(
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": self._build_audio_data_url(asset.storage_uri, asset.mime_type),
                        },
                    }
                )
            elif asset.asset_type == "image":
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": self._build_image_data_url(asset.storage_uri, asset.mime_type),
                            "detail": "auto",
                        },
                    }
                )
        messages.append({"role": "user", "content": content})
        return messages

    def _build_chat_completion_tools(self, allowed_tool_names: set[str] | None) -> list[dict[str, Any]]:
        """把 ToolRegistry 中的工具导出成 Chat Completions schema。"""

        tools: list[dict[str, Any]] = []
        for tool in self._tool_registry.list_tools(allowed_names=allowed_tool_names):
            parameters = tool.spec.input_model.model_json_schema()
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.spec.name,
                        "description": tool.spec.description,
                        "parameters": parameters,
                    },
                }
            )
        return tools

    @staticmethod
    def _build_audio_data_url(storage_uri: str, mime_type: str) -> str:
        """把本地 WAV 文件转成 `data:` URL。"""

        with open(storage_uri, "rb") as handle:
            payload = handle.read()
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{mime_type or 'audio/wav'};base64,{encoded}"

    @staticmethod
    def _extract_chat_completion_message(completion: object) -> object:
        """从非流式 Chat Completions 返回中取出第一条消息。"""

        choices = getattr(completion, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise build_error(ErrorCode.INTERNAL_ERROR, "模型返回缺少 choices")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise build_error(ErrorCode.INTERNAL_ERROR, "模型返回缺少 message")
        return message

    @staticmethod
    def _extract_chat_message_text(message: object) -> str:
        """从 Chat Completions message 中提取文本。"""

        content = message.get("content", "") if isinstance(message, dict) else getattr(message, "content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                else:
                    text = getattr(item, "text", None)
                if isinstance(text, str):
                    parts.append(text)
            return "".join(parts)
        return ""

    @staticmethod
    def _extract_chat_tool_calls(message: object) -> list[dict[str, str]]:
        """把 SDK message.tool_calls 统一转成字典列表。"""

        raw_tool_calls = getattr(message, "tool_calls", None)
        if not isinstance(raw_tool_calls, list):
            return []
        calls: list[dict[str, str]] = []
        for call in raw_tool_calls:
            function = getattr(call, "function", None)
            name = getattr(function, "name", "") if function is not None else ""
            if not name:
                continue
            calls.append(
                {
                    "id": str(getattr(call, "id", "")),
                    "type": str(getattr(call, "type", "function") or "function"),
                    "name": str(name),
                    "arguments": str(getattr(function, "arguments", "{}") or "{}"),
                }
            )
        return calls

    def _consume_direct_chat_stream(
        self,
        *,
        completion: object,
        reply_text_delta_callback: Callable[[str], None] | None,
    ) -> tuple[str, list[dict[str, str]]]:
        """消费音频原生 Chat Completions 流式响应。

        主要逻辑：
        1. 持续提取 `delta.content` 文本并透传给上层 TTS。
        2. 累积 `delta.tool_calls` 分片，直到流结束后得到完整工具调用。
        3. 同时兼容对象和字典两种 OpenAI-compatible SDK 结构。

        参数：
        1. `completion`：`chat.completions.create(stream=True)` 返回的可迭代对象。
        2. `reply_text_delta_callback`：文本增量回调，可为空。

        返回值：
        1. `(reply_text, tool_calls)`，其中 `tool_calls` 为统一字典结构。

        异常情况：
        1. 流式分片缺少 choices 时会跳过该分片；工具名为空的调用会被过滤。
        """

        reply_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, str]] = {}
        for chunk in completion:
            text_delta = self._extract_stream_text_delta(chunk)
            if text_delta:
                reply_parts.append(text_delta)
                if reply_text_delta_callback is not None:
                    reply_text_delta_callback(text_delta)
            for tool_delta in self._extract_stream_tool_call_deltas(chunk):
                index = self._coerce_tool_call_index(self._get_value(tool_delta, "index"), len(tool_call_parts))
                current = tool_call_parts.setdefault(
                    index,
                    {"id": "", "type": "function", "name": "", "arguments": ""},
                )
                call_id = self._get_value(tool_delta, "id")
                call_type = self._get_value(tool_delta, "type")
                if isinstance(call_id, str) and call_id:
                    current["id"] = call_id
                if isinstance(call_type, str) and call_type:
                    current["type"] = call_type
                function = self._get_value(tool_delta, "function")
                name = self._get_value(function, "name") if function is not None else None
                arguments = self._get_value(function, "arguments") if function is not None else None
                if isinstance(name, str) and name:
                    current["name"] += name
                if isinstance(arguments, str) and arguments:
                    current["arguments"] += arguments
        tool_calls: list[dict[str, str]] = []
        for index in sorted(tool_call_parts):
            call = tool_call_parts[index]
            if not call["name"]:
                continue
            tool_calls.append(
                {
                    "id": call["id"] or f"call_{index}",
                    "type": call["type"] or "function",
                    "name": call["name"],
                    "arguments": call["arguments"] or "{}",
                }
            )
        return "".join(reply_parts), tool_calls

    @classmethod
    def _extract_stream_tool_call_deltas(cls, chunk: object) -> list[object]:
        """从 Chat Completions 流式分片里提取工具调用增量。"""

        choices = cls._get_value(chunk, "choices")
        if not isinstance(choices, list) or not choices:
            return []
        delta = cls._get_value(choices[0], "delta")
        tool_calls = cls._get_value(delta, "tool_calls") if delta is not None else None
        return tool_calls if isinstance(tool_calls, list) else []

    @staticmethod
    def _get_value(source: object, key: str) -> object:
        """同时兼容对象属性和字典字段读取。"""

        if isinstance(source, dict):
            return source.get(key)
        return getattr(source, key, None)

    @staticmethod
    def _coerce_tool_call_index(value: object, fallback: int) -> int:
        """把工具调用 index 转成整数，缺失时使用当前累积顺序。"""

        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
        return fallback

    @staticmethod
    def _build_assistant_tool_call_message(message: object, tool_calls: list[dict[str, str]]) -> dict[str, Any]:
        """构造回填给模型的 assistant tool_calls 消息。"""

        return {
            "role": "assistant",
            "content": OpenAIAgentLoopRunner._extract_chat_message_text(message) or "",
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": call["type"],
                    "function": {
                        "name": call["name"],
                        "arguments": call["arguments"],
                    },
                }
                for call in tool_calls
            ],
        }

    @staticmethod
    def _build_history_messages(session: AgentSession, turn: AgentTurn) -> list[dict[str, Any]]:
        """按原始历史消息构造模型输入消息列表。

        主要逻辑：
        1. 直接复用会话中的 `user/assistant` 历史消息，不再自行压缩成说明文本。
        2. 排除当前 turn 已经落入会话中的实时用户消息，避免重复。
        3. 将当前轮 ASR 文本与本轮自动照片一起作为最后一条 `user` 消息追加。

        参数：
        1. `session`：当前会话对象。
        2. `turn`：当前轮输入对象。

        返回值：
        1. 适合直接传给 Agents SDK 的消息列表。
        """

        messages: list[dict[str, Any]] = []
        for message in session.messages:
            if message.meta.get("turn_id") == turn.turn_id:
                continue
            if message.role not in {"user", "assistant"}:
                continue
            text = message.text.strip()
            if not text:
                continue
            messages.append(
                {
                    "role": message.role,
                    "content": text,
                }
            )
        messages.append({"role": "user", "content": OpenAIAgentLoopRunner._build_current_turn_content(turn)})
        return messages

    @staticmethod
    def _summarize_for_log(value: Any, *, max_chars: int = 2000) -> str:
        """生成适合 DEBUG 日志的短摘要。

        主要逻辑：
        1. 优先转成 JSON，便于排查工具入参和返回值。
        2. 对不可序列化对象退回 `str()`。
        3. 限制最大长度，避免图片、音频或长文本结果刷屏。

        参数：
        1. `value`：需要写入日志的任意对象。
        2. `max_chars`：日志摘要最大字符数。

        返回值：
        1. 截断后的字符串摘要。
        """

        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except TypeError:
            text = str(value)
        if len(text) <= max_chars:
            return text
        return f"{text[:max_chars]}...(truncated, chars={len(text)})"

    @staticmethod
    def _build_current_turn_content(turn: AgentTurn) -> str | list[dict[str, Any]]:
        """构造当前轮用户消息内容。

        主要逻辑：
        1. 没有图片时沿用纯文本输入，保持普通工具链路兼容。
        2. 如果当前 turn 挂接了图片资产，则使用 OpenAI Agents SDK 支持的
           `input_text/input_image` 内容结构。
        3. 图片使用 `data:` URL，避免额外对象存储依赖。

        参数：
        1. `turn`：当前用户输入轮次。

        返回值：
        1. 纯文本字符串，或 OpenAI Agents SDK 兼容的多模态 content 列表。

        异常情况：
        1. 图片文件不存在或读取失败时会向上抛出异常，由上层统一转为 agent-core 失败。
        """

        text = turn.input_text.strip()
        image_assets = [asset for asset in turn.asset_refs if asset.asset_type == "image"]
        if not image_assets:
            return text

        content: list[dict[str, Any]] = [{"type": "input_text", "text": text}]
        for asset in image_assets:
            content.append(
                {
                    "type": "input_image",
                    "image_url": OpenAIAgentLoopRunner._build_image_data_url(asset.storage_uri, asset.mime_type),
                    "detail": "auto",
                }
            )
        return content

    @staticmethod
    def _sanitize_model_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """生成可持久化的模型请求快照。

        主要逻辑：
        1. 保留消息角色、文本和多模态结构，便于回归排障。
        2. 将图片和音频 `data:` URL 替换为占位符，避免日志和结果文件保存大段 base64。

        参数：
        1. `messages`：真实发送给模型的消息列表。

        返回值：
        1. 可写入日志或 `model_request` 的脱敏消息列表。
        """

        sanitized: list[dict[str, Any]] = []
        for message in messages:
            content = message.get("content")
            if not isinstance(content, list):
                sanitized.append(dict(message))
                continue
            sanitized_content: list[dict[str, Any]] = []
            for item in content:
                if not isinstance(item, dict):
                    sanitized_content.append(item)
                    continue
                if item.get("type") == "image_url":
                    image_url = dict(item.get("image_url") or {})
                    url = str(image_url.get("url") or "")
                    if url.startswith("data:"):
                        image_url["url"] = "data:image/*;base64,<redacted>"
                    sanitized_content.append({**item, "image_url": image_url})
                    continue
                if item.get("type") == "input_image":
                    image_url = str(item.get("image_url") or "")
                    sanitized_item = dict(item)
                    if image_url.startswith("data:"):
                        sanitized_item["image_url"] = "data:image/*;base64,<redacted>"
                    sanitized_content.append(sanitized_item)
                    continue
                if item.get("type") == "input_audio":
                    input_audio = dict(item.get("input_audio") or {})
                    data = str(input_audio.get("data") or "")
                    if data.startswith("data:"):
                        input_audio["data"] = "data:audio/*;base64,<redacted>"
                    sanitized_content.append({**item, "input_audio": input_audio})
                    continue
                else:
                    sanitized_content.append(dict(item) if isinstance(item, dict) else item)
                    continue
            sanitized.append({**message, "content": sanitized_content})
        return sanitized

    def _build_instructions(self, session_id: str | None = None) -> str:
        """构造最小 Agent 指令。"""

        if self._settings.enable_progress_message:
            tool_prompt = (
                "需要时可以调用已提供的工具。工具执行前 SDK 会按工具配置播报等待提示，"
                "你不要重复输出等待提示。\n\n"
            )
        else:
            tool_prompt = (
                "需要时可以调用已提供的工具，因为工具的执行需要时间，调用前请先简单回复用户，"
                "注意不要提及工具名称、参数等，并且不要提前说已经完成，因为工具的执行可能失败。\n\n"
            )

        base = (
            f"{self._settings.voice_system_prompt}\n"
            "如果用户的问题不包含关于图片的问题，请不要专门对图片的内容给出解释。\n"
            f"{tool_prompt}"
            "你应当使用 manage_memory 工具主动维护关于用户的记忆，包括新增、更新、删除。\n"
            "例如：姓名、年龄、性别、称呼、语言偏好、沟通偏好、住址、常去地点、联系人称呼、导航偏好、出行习惯、饮食偏好、无障碍偏好、提醒或任务设置等。\n\n"
            "当用户的问题涉及到出行规划、行动建议等与个人习惯、偏好、经验相关的话题时，要主动使用 memory_search 工具查询你关注的记忆主题。\n"
        )
        if self._skill_runtime is None or not session_id:
            return base
        fragment = self._skill_runtime.build_prompt_fragment(session_id=session_id)
        if not fragment:
            return base
        return f"{base}\n{fragment}\n"

    @staticmethod
    def _build_image_followup_instructions() -> str:
        """构造拍照后主链路图片解读提示词。

        主要逻辑：
        1. 明确当前已经拿到用户眼前真实照片。
        2. 要求模型直接根据图片回答原问题。
        3. 禁止只说明“已经拍照”或转而追问保存照片之类的无关问题。

        返回值：
        1. 供多模态图片解读使用的系统提示词。
        """

        return (
            "你的名字是'乐鑫'。你是盲人眼镜上的中文语音助手。\n"
            "现在你已经拿到了用户眼前场景的真实照片，请直接结合图片回答用户刚才的问题。\n"
            "如果图片太暗、太糊或关键信息看不清，要明确说明看不清的地方。\n"
            "不要只说你已经拍照了，也不要追问是否保存照片，除非用户明确问保存相关的事。\n"
            "请使用简短、口语化、直接的中文回答。\n"
            "不要输出代码块。\n"
        )

    @staticmethod
    def _extract_reply_text(final_output: object) -> str:
        """从 SDK 结果中提取最终回复文本。

        主要逻辑：
        1. 优先直接读取字符串结果。
        2. 若 provider 返回带 `reply_text` 的对象或字典，则兜底提取。
        3. 其他情况统一转成字符串后再裁剪。

        参数：
        1. `final_output`：Agents SDK 返回的最终结果对象。

        返回值：
        1. 去除首尾空白后的最终回复文本；若无法提取则返回空字符串。
        """

        if isinstance(final_output, str):
            return final_output.strip()
        if isinstance(final_output, dict):
            reply_text = final_output.get("reply_text")
            return str(reply_text).strip() if reply_text is not None else ""
        if hasattr(final_output, "reply_text"):
            reply_text = getattr(final_output, "reply_text")
            return str(reply_text).strip() if reply_text is not None else ""
        if final_output is None:
            return ""
        return str(final_output).strip()
