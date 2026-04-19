"""agent-core 运行循环实现。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from agent_core.context.models import AgentSession, AgentTurn, AgentTurnResult
from agent_core.context.session_store import AgentSessionStore
from agent_core.tools import AgentToolContext, ToolGateway, ToolRegistry
from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug


class AgentLoopRunner(ABC):
    """Agent Loop 抽象接口。"""

    @abstractmethod
    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
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
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._tool_gateway = tool_gateway
        self._logger = get_logger("server.agent.runner")

    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
        """执行一轮 AgentTurn。"""

        capability_traces = []
        tool_context = AgentToolContext(
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            settings=self._settings,
            session_store=self._session_store,
            device_state_reader=self._tool_registry.get_device_state_reader(),
            trace_sink=capability_traces.append,
            task_gateway=self._tool_registry.get_task_gateway(),
            camera_gateway=self._tool_registry.get_camera_gateway(),
            tool_gateway=self._tool_gateway,
            skill_gateway=self._tool_registry.get_skill_gateway(),
            mcp_gateway=self._tool_registry.get_mcp_gateway(),
        )

        if not self._settings.dashscope_api_key.strip():
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="缺少 DASHSCOPE_API_KEY，无法执行 agent-core 运行循环",
                    traces=capability_traces,
                    error=build_error(
                        ErrorCode.INVALID_CONFIG,
                        "缺少 DASHSCOPE_API_KEY，无法执行 agent-core 运行循环",
                    ),
                ),
                context=tool_context,
            )

        try:
            from agents import Agent, MultiProvider, RunConfig, Runner
        except ImportError:
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="缺少 openai-agents 依赖，无法执行 agent-core 运行循环",
                    traces=capability_traces,
                    error=build_error(
                        ErrorCode.INVALID_CONFIG,
                        "缺少 openai-agents 依赖，无法执行 agent-core 运行循环",
                        details={"hint": "请执行 uv sync 安装 openai-agents"},
                    ),
                ),
                context=tool_context,
            )

        instructions = self._build_instructions()
        run_input = self._build_history_messages(session=session, turn=turn)
        model_request = {
            "model": self._settings.agent_model_name,
            "instructions": instructions,
            "messages": [
                {"role": "system", "content": instructions},
                *run_input,
            ],
        }

        agent = Agent(
            name="OpenAIGlassesAgent",
            instructions=instructions,
            tools=self._tool_registry.list_sdk_tools(),
            model=self._settings.agent_model_name,
        )
        provider = MultiProvider(
            openai_api_key=self._settings.dashscope_api_key,
            openai_base_url=self._settings.voice_model_base_url,
            openai_use_responses=False,
        )
        run_config = RunConfig(
            model=self._settings.agent_model_name,
            model_provider=provider,
            tracing_disabled=True,
            workflow_name="OpenAI Glasses Agent Loop",
            group_id=turn.session_id,
        )
        log_debug(
            self._logger,
            f"agent-core 即将运行: model={self._settings.agent_model_name} message_count={len(run_input) + 1}",
            LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
        )

        try:
            run_result = self._run_with_thread_event_loop(
                lambda: Runner.run_sync(
                    agent,
                    run_input,
                    context=tool_context,
                    max_turns=6,
                    run_config=run_config,
                    conversation_id=turn.session_id,
                )
            )
        except Exception as exc:
            log_debug(
                self._logger,
                f"agent-core 运行异常: reason={exc!r}",
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="agent-core 运行失败",
                    traces=capability_traces,
                    error=build_error(
                        ErrorCode.INTERNAL_ERROR,
                        "agent-core 运行失败",
                        details={"reason": str(exc)},
                    ),
                ),
                context=tool_context,
            )

        reply_text = self._extract_reply_text(run_result.final_output)
        if not reply_text:
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="agent-core 返回了空回复",
                    traces=capability_traces,
                    error=build_error(
                        ErrorCode.INTERNAL_ERROR,
                        "agent-core 返回了空回复",
                    ),
                ),
                context=tool_context,
            )

        return self._attach_capability_outputs(
            result=AgentTurnResult(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                device_id=turn.device_id,
                reply_text=reply_text or "抱歉，我现在还没法稳定回答这个问题。",
                capability_traces=capability_traces,
                meta={"model_request": model_request},
            ),
            context=tool_context,
        )

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
    def _build_history_messages(session: AgentSession, turn: AgentTurn) -> list[dict[str, str]]:
        """按原始历史消息构造模型输入消息列表。

        主要逻辑：
        1. 直接复用会话中的 `user/assistant` 历史消息，不再自行压缩成说明文本。
        2. 排除当前 turn 已经落入会话中的实时用户消息，避免重复。
        3. 将当前轮 ASR 文本作为最后一条 `user` 消息追加。

        参数：
        1. `session`：当前会话对象。
        2. `turn`：当前轮输入对象。

        返回值：
        1. 适合直接传给 Agents SDK 的消息列表。
        """

        messages: list[dict[str, str]] = []
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
        messages.append(
            {
                "role": "user",
                "content": turn.input_text.strip(),
            }
        )
        return messages

    def _build_instructions(self) -> str:
        """构造最小 Agent 指令。"""

        return (
            f"{self._settings.voice_system_prompt}\n"
            "请使用简短、口语化、直接的中文回答。\n"
            "必要时可以调用已提供的工具。\n"
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
