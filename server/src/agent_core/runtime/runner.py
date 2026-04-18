"""agent-core 运行循环实现。"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from agent_core.context.assembler import ContextAssembler
from agent_core.context.models import AgentSession, AgentTurn, AgentTurnResult
from agent_core.context.session_store import AgentSessionStore
from agent_core.tools import AgentToolContext, ToolGateway, ToolRegistry
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug


class StructuredAgentReply(BaseModel):
    """Agent 最终结构化输出。"""

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
        context_assembler: ContextAssembler | None = None,
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._tool_gateway = tool_gateway
        self._context_assembler = context_assembler or ContextAssembler()
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
            tool_gateway=self._tool_gateway,
            skill_gateway=self._tool_registry.get_skill_gateway(),
            mcp_gateway=self._tool_registry.get_mcp_gateway(),
        )

        if self._should_force_query_device_state(turn.input_text):
            result = self._run_direct_device_state_query(turn=turn, context=tool_context, traces=capability_traces)
            return self._attach_capability_outputs(result=result, context=tool_context)

        direct_route_result = self._run_direct_capability_route(turn=turn, context=tool_context, traces=capability_traces)
        if direct_route_result is not None:
            return self._attach_capability_outputs(result=direct_route_result, context=tool_context)

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

        final_output = run_result.final_output
        if isinstance(final_output, StructuredAgentReply):
            action = final_output.action
            reply_text = final_output.reply_text.strip()
        elif isinstance(final_output, str):
            action = "final_answer"
            reply_text = final_output.strip()
        else:
            return self._attach_capability_outputs(
                result=self._build_failure_result(
                    turn=turn,
                    message="agent-core 返回了无法识别的最终输出",
                    traces=capability_traces,
                    error=build_error(
                        ErrorCode.INTERNAL_ERROR,
                        "agent-core 返回了无法识别的最终输出",
                        details={"final_output_type": type(final_output).__name__},
                    ),
                ),
                context=tool_context,
            )

        return self._attach_capability_outputs(
            result=AgentTurnResult(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                device_id=turn.device_id,
                action=action,
                reply_text=reply_text or "抱歉，我现在还没法稳定回答这个问题。",
                capability_traces=capability_traces,
            ),
            context=tool_context,
        )

    def _run_direct_device_state_query(
        self,
        *,
        turn: AgentTurn,
        context: AgentToolContext,
        traces: list,
    ) -> AgentTurnResult:
        """执行设备状态的直连查询。"""

        log_debug(
            self._logger,
            f"命中设备状态直连路由，直接调用 query_device_state: input={turn.input_text!r}",
            LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
        )
        try:
            device_state = self._tool_gateway.invoke(
                name="query_device_state",
                context=context,
            )
        except Exception as exc:
            log_debug(
                self._logger,
                f"设备状态直连路由失败: reason={exc!r}",
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )
            error = exc if isinstance(exc, AppError) else build_error(
                ErrorCode.INTERNAL_ERROR,
                "query_device_state 调用失败",
                details={"reason": str(exc)},
            )
            return self._build_failure_result(
                turn=turn,
                message=error.message,
                traces=traces,
                error=error,
            )

        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            action="final_answer",
            reply_text=self._build_device_state_reply(device_state.data),
            capability_traces=traces,
        )

    def _run_direct_capability_route(
        self,
        *,
        turn: AgentTurn,
        context: AgentToolContext,
        traces: list,
    ) -> AgentTurnResult | None:
        """处理 Phase E 的直连能力路由。"""

        text = turn.input_text.strip()
        if not text:
            return None

        if self._should_force_photo_interpret(text):
            result = self._tool_gateway.invoke(
                name="photo_interpret",
                context=context,
                arguments={
                    "question": text,
                    "capture_first": True,
                },
            )
            return AgentTurnResult(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                device_id=turn.device_id,
                action="final_answer",
                reply_text=str(result.data.get("answer_text") or result.message or "我已经看过图片了。"),
                capability_traces=traces,
            )

        if self._should_force_timer_manage(text):
            result = self._tool_gateway.invoke(
                name="timer_manage",
                context=context,
                arguments=self._build_timer_manage_arguments(text),
            )
            return AgentTurnResult(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                device_id=turn.device_id,
                action="final_answer",
                reply_text=str(result.data.get("summary") or result.message or "计时器操作已完成。"),
                capability_traces=traces,
            )

        if self._should_force_amap_route_plan(text):
            result = self._tool_gateway.invoke(
                name="amap_route_plan",
                context=context,
                arguments=self._build_amap_route_arguments(text),
            )
            return AgentTurnResult(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                device_id=turn.device_id,
                action="final_answer",
                reply_text=str(result.data.get("summary") or result.message or "路线已规划。"),
                capability_traces=traces,
            )

        return None

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
            action="fail",
            reply_text=f"抱歉，这一轮处理失败了：{message}",
            capability_traces=traces,
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
    def _should_force_photo_interpret(text: str) -> bool:
        photo_keywords = ("拍照", "看看前面", "照片", "画面", "障碍")
        return any(keyword in text for keyword in photo_keywords)

    @staticmethod
    def _should_force_timer_manage(text: str) -> bool:
        timer_keywords = ("计时", "定时", "倒计时")
        return any(keyword in text for keyword in timer_keywords)

    @staticmethod
    def _should_force_amap_route_plan(text: str) -> bool:
        nav_keywords = ("导航", "带我去", "去最近的", "路线")
        return any(keyword in text for keyword in nav_keywords)

    @staticmethod
    def _build_timer_manage_arguments(text: str) -> dict[str, object]:
        duration_seconds = 300
        digits = []
        current_digits = ""
        for char in text:
            if char.isdigit():
                current_digits += char
            elif current_digits:
                digits.append(int(current_digits))
                current_digits = ""
        if current_digits:
            digits.append(int(current_digits))
        if digits:
            first = digits[0]
            duration_seconds = first * 60 if "分钟" in text else first
        return {
            "action": "create",
            "duration_seconds": duration_seconds,
            "query": text,
        }

    @staticmethod
    def _build_amap_route_arguments(text: str) -> dict[str, object]:
        destination = text
        for prefix in ("导航去", "带我去", "去最近的", "去", "导航到"):
            if prefix in text:
                destination = text.split(prefix, 1)[1].strip(" ，。？?")
                break
        destination = destination or "最近的目的地"
        return {
            "origin": "当前设备位置",
            "destination": destination,
            "strategy": "walking",
        }

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
        """构造最小 Agent 指令。"""

        return (
            f"{self._settings.voice_system_prompt}\n"
            "你现在运行在智能眼镜服务端的 agent-core 中。\n"
            "请遵守以下规则：\n"
            "1. 当前阶段最终只允许输出 `final_answer` 或 `ask_user`。\n"
            "2. 当用户询问设备状态、是否在线、是否正在监听时，应优先调用 `query_device_state`。\n"
            "3. 当用户要求拍照看前方时，可调用 `photo_interpret` 或 `capture_photo`。\n"
            "4. 当用户要求创建计时器时，可调用 `timer_manage` 或计时器相关 Tool。\n"
            "5. 当用户询问路线或导航时，可调用 `amap_route_plan`。\n"
            "6. 若信息不足，需要用简短中文追问，并输出 `ask_user`。\n"
            "7. 回复必须简短、口语化、直接，不要输出代码块。\n"
        )
