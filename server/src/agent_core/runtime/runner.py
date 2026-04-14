"""agent-core 运行循环实现。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agent_core.context.assembler import ContextAssembler
from agent_core.context.models import AgentSession, AgentTurn, AgentTurnResult
from agent_core.tools import AgentToolContext, ToolRegistry
from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug


class StructuredAgentReply(BaseModel):
    """Agent 最终结构化输出。

    主要功能：
    1. 约束模型最终只能输出当前阶段支持的动作。
    2. 把对话回复文本与动作一起回传给 `voice-runtime`。
    """

    action: Literal["final_answer", "ask_user"] = Field(description="当前轮最终动作")
    reply_text: str = Field(description="当前轮需要返回给用户的中文文本")

    @model_validator(mode="before")
    @classmethod
    def unwrap_single_item_list(cls, value):
        """兼容模型偶发返回 `[{...}]` 的情况。"""

        if isinstance(value, list) and len(value) == 1:
            return value[0]
        return value


class AgentLoopRunner(ABC):
    """Agent Loop 抽象接口。"""

    @abstractmethod
    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
        """执行一轮 AgentTurn。

        参数：
        1. `session`：当前会话对象。
        2. `turn`：当前轮输入对象。

        返回值：
        1. `AgentTurnResult`。
        """


class OpenAIAgentLoopRunner(AgentLoopRunner):
    """基于 OpenAI Agents SDK 的最小运行循环。

    主要功能：
    1. 把会话历史和当前输入装配为 Agent 输入。
    2. 通过 OpenAI Agents SDK 执行工具调用与最终回复生成。
    3. 把 Tool 调用轨迹汇总回 `AgentTurnResult`。
    """

    def __init__(
        self,
        *,
        settings: ServerSettings,
        tool_registry: ToolRegistry,
        context_assembler: ContextAssembler | None = None,
    ) -> None:
        self._settings = settings
        self._tool_registry = tool_registry
        self._context_assembler = context_assembler or ContextAssembler()
        self._logger = get_logger("server.agent.runner")

    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
        """执行一轮 AgentTurn。

        主要逻辑：
        1. 构造 OpenAI Agents SDK 所需的 Agent、Tool 上下文和运行配置。
        2. 提交短期历史与当前轮文本。
        3. 读取结构化最终输出并转为统一结果对象。

        异常情况：
        1. API Key 缺失或 SDK 调用失败时抛出结构化错误。
        """

        if not self._settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行 agent-core 运行循环",
            )

        try:
            from agents import Agent, MultiProvider, RunConfig, Runner
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 openai-agents 依赖，无法执行 agent-core 运行循环",
                details={"hint": "请执行 uv sync 安装 openai-agents"},
            ) from exc

        capability_traces = []
        tool_context = AgentToolContext(
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            device_state_reader=self._tool_registry.get_device_state_reader(),
            trace_sink=capability_traces.append,
        )
        if self._should_force_query_device_state(turn.input_text):
            log_debug(
                self._logger,
                f"命中设备状态直连路由，直接调用 query_device_state: input={turn.input_text!r}",
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )
            device_state = self._tool_registry.invoke(
                name="query_device_state",
                context=tool_context,
            )
            return AgentTurnResult(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                device_id=turn.device_id,
                action="final_answer",
                reply_text=self._build_device_state_reply(device_state),
                capability_traces=capability_traces,
            )

        agent = Agent(
            name="OpenAIGlassesAgent",
            instructions=self._build_instructions(),
            tools=self._tool_registry.list_sdk_tools(),
            model=self._settings.voice_model_name,
            output_type=StructuredAgentReply,
        )
        provider = MultiProvider(
            openai_api_key=self._settings.dashscope_api_key,
            openai_base_url=self._settings.voice_model_base_url,
            openai_use_responses=False,
        )
        run_config = RunConfig(
            model=self._settings.voice_model_name,
            model_provider=provider,
            tracing_disabled=True,
            workflow_name="OpenAI Glasses Agent Loop",
            group_id=turn.session_id,
        )
        run_input = self._context_assembler.assemble_turn_input(session=session, turn=turn)
        log_debug(
            self._logger,
            f"agent-core 即将运行: model={self._settings.voice_model_name} input={run_input!r}",
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
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "agent-core 运行失败",
                details={"reason": str(exc)},
            ) from exc

        final_output = run_result.final_output
        if isinstance(final_output, StructuredAgentReply):
            action = final_output.action
            reply_text = final_output.reply_text.strip()
        elif isinstance(final_output, str):
            action = "final_answer"
            reply_text = final_output.strip()
        else:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "agent-core 返回了无法识别的最终输出",
                details={"final_output_type": type(final_output).__name__},
            )

        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            action=action,
            reply_text=reply_text or "抱歉，我现在还没法稳定回答这个问题。",
            capability_traces=capability_traces,
        )

    @staticmethod
    def _run_with_thread_event_loop(callback):
        """确保当前线程存在可用 event loop 后再执行回调。

        主要逻辑：
        1. 读取当前线程绑定的 event loop。
        2. 若当前线程尚无 loop，或 loop 已关闭，则创建临时 loop。
        3. 回调执行完成后关闭临时 loop，避免泄漏。

        参数：
        1. `callback`：需要在有 event loop 环境中执行的同步函数。

        返回值：
        1. 回调返回值。
        """

        previous_loop = None
        created_loop = None

        try:
            previous_loop = asyncio.get_event_loop()
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
    def _should_force_query_device_state(input_text: str) -> bool:
        """判断当前输入是否应直接查询设备状态。"""

        text = input_text.strip()
        if not text:
            return False

        status_keywords = ("状态", "怎么样", "在线", "监听", "正常", "情况")
        subject_keywords = ("眼镜", "设备")
        if any(keyword in text for keyword in subject_keywords) and any(keyword in text for keyword in status_keywords):
            return True

        direct_patterns = (
            "我的眼镜现在怎么样了",
            "我的眼镜怎么样了",
            "眼镜现在怎么样",
            "眼镜现在还在线吗",
            "眼镜还在线吗",
            "眼镜还在监听吗",
            "设备现在怎么样",
            "设备还在线吗",
        )
        return any(pattern in text for pattern in direct_patterns)

    @staticmethod
    def _build_device_state_reply(device_state: dict[str, object]) -> str:
        """把结构化设备状态转换为口语化回复。"""

        state = str(device_state.get("state") or "unknown")
        audio_online = bool(device_state.get("audio_connection_online", False))

        if state == "listening":
            return "眼镜在线，正在待命监听。"
        if state == "receiving_segment":
            return "眼镜在线，正在收音。"
        if state == "model_running":
            return "眼镜在线，正在处理你刚才的问题。"
        if state in {"replying", "reply_streaming"}:
            return "眼镜在线，正在播报回复。"
        if state == "opened":
            return "眼镜在线，语音会话刚打开。"
        if state == "failed":
            return "眼镜在线，但刚才那轮处理失败了。"
        if state == "closed":
            return "眼镜当前没有处于可用会话里。"
        if audio_online:
            return f"眼镜在线，当前状态是 {state}。"
        return f"眼镜主连接在线，但音频链路当前不在线，状态是 {state}。"

    def _build_instructions(self) -> str:
        """构造最小 Agent 指令。

        返回值：
        1. 适用于 Phase D 的系统级中文提示词。
        """

        return (
            f"{self._settings.voice_system_prompt}\n"
            "你现在运行在智能眼镜服务端的 agent-core 中。\n"
            "请遵守以下规则：\n"
            "1. 当前阶段最终只允许输出 `final_answer` 或 `ask_user`。\n"
            "2. 当用户询问设备状态、是否在线、是否正在监听时，应优先调用 `query_device_state`。\n"
            "3. 若信息不足，需要用简短中文追问，并输出 `ask_user`。\n"
            "4. 回复必须简短、口语化、直接，不要输出代码块。\n"
        )
