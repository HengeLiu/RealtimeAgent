"""agent-core 对外统一门面。"""

from __future__ import annotations

from agent_core.context import AgentSessionStore, AgentTurn, AgentTurnResult, MessageContext, generate_id
from agent_core.context.models import CapabilityTrace, MediaAssetRef
from agent_core.runtime import AgentLoopRunner, OpenAIAgentLoopRunner
from agent_core.tools import ToolRegistry
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug


class AgentFacade:
    """agent-core 对外统一入口。

    主要功能：
    1. 接收 `voice-runtime` 提交的 `AgentTurn`。
    2. 维护会话上下文、资产引用和调用轨迹。
    3. 调用底层 Agent Loop 并返回统一结果对象。
    """

    def __init__(
        self,
        *,
        session_store: AgentSessionStore,
        tool_registry: ToolRegistry,
        runner: AgentLoopRunner,
    ) -> None:
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._runner = runner
        self._logger = get_logger("server.agent")

    @classmethod
    def build_default(
        cls,
        *,
        settings: ServerSettings,
        device_state_reader,
    ) -> "AgentFacade":
        """构建默认门面实例。

        参数：
        1. `settings`：服务端配置。
        2. `device_state_reader`：设备状态读取函数。

        返回值：
        1. 默认 `AgentFacade`。
        """

        session_store = AgentSessionStore()
        tool_registry = ToolRegistry(device_state_reader=device_state_reader)
        runner = OpenAIAgentLoopRunner(settings=settings, tool_registry=tool_registry)
        return cls(
            session_store=session_store,
            tool_registry=tool_registry,
            runner=runner,
        )

    def handle_turn(self, turn: AgentTurn) -> AgentTurnResult:
        """处理一轮 Agent 输入。

        主要逻辑：
        1. 获取或创建会话。
        2. 保存当前轮资产与派生结果。
        3. 先写入用户消息，再执行 Agent Loop。
        4. 把最终回复、轨迹和失败信息回写到上下文。

        参数：
        1. `turn`：当前轮输入对象。

        返回值：
        1. `AgentTurnResult`。
        """

        session = self._session_store.get_or_create_session(
            session_id=turn.session_id,
            device_id=turn.device_id,
        )
        asset_ids = self._session_store.save_assets(session_id=turn.session_id, assets=turn.asset_refs)
        artifact_ids = self._session_store.save_artifacts(session_id=turn.session_id, artifacts=turn.derived_artifacts)

        self._session_store.append_message(
            session_id=turn.session_id,
            message=MessageContext(
                message_id=generate_id("msg"),
                session_id=turn.session_id,
                role="user",
                kind="audio_input",
                text=turn.input_text,
                asset_refs=asset_ids,
                derived_refs=artifact_ids,
                meta={
                    "turn_id": turn.turn_id,
                    "source": turn.source,
                    **turn.meta,
                },
            ),
        )

        try:
            result = self._runner.run_turn(session=session, turn=turn)
        except AppError as exc:
            log_debug(
                self._logger,
                f"agent-core 返回结构化失败: error={exc.to_dict()} input_text={turn.input_text!r}",
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )
            result = self._build_failure_result(turn=turn, error=exc, traces=[])
        except Exception as exc:
            error = build_error(
                ErrorCode.INTERNAL_ERROR,
                "agent-core 处理当前输入失败",
                details={"reason": str(exc)},
            )
            log_debug(
                self._logger,
                f"agent-core 返回非结构化失败: error={error.to_dict()} input_text={turn.input_text!r}",
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )
            result = self._build_failure_result(turn=turn, error=error, traces=[])

        result = self._persist_result(turn=turn, result=result)
        return result

    def _persist_result(self, *, turn: AgentTurn, result: AgentTurnResult) -> AgentTurnResult:
        """把运行结果统一写回会话上下文。"""

        assistant_message_id = generate_id("msg")
        self._session_store.append_capability_traces(
            session_id=turn.session_id,
            traces=result.capability_traces,
        )
        self._session_store.append_message(
            session_id=turn.session_id,
            message=MessageContext(
                message_id=assistant_message_id,
                session_id=turn.session_id,
                role="assistant",
                kind="assistant_question" if result.action == "ask_user" else "assistant_reply",
                text=result.reply_text,
                meta={
                    "turn_id": turn.turn_id,
                    "action": result.action,
                    "capability_trace_ids": [trace.trace_id for trace in result.capability_traces],
                },
            ),
        )

        session = self._session_store.get_session(turn.session_id)
        if session is not None:
            if result.action == "ask_user":
                session.dialog_state.pending_question = result.reply_text
            else:
                session.dialog_state.pending_question = None
                session.dialog_state.missing_slots.clear()

        result.assistant_message_id = assistant_message_id
        return result

    def attach_assistant_asset(
        self,
        *,
        session_id: str,
        assistant_message_id: str,
        asset: MediaAssetRef,
    ) -> None:
        """把生成后的助手媒体资产挂到消息上。

        参数：
        1. `session_id`：会话编号。
        2. `assistant_message_id`：目标助手消息编号。
        3. `asset`：待挂接的媒体资产引用。
        """

        asset_ids = self._session_store.save_assets(session_id=session_id, assets=[asset])
        self._session_store.attach_assets_to_message(
            session_id=session_id,
            message_id=assistant_message_id,
            asset_ids=asset_ids,
        )

    def get_session_store(self) -> AgentSessionStore:
        """返回内部会话存储。

        返回值：
        1. `AgentSessionStore`。
        """

        return self._session_store

    def _build_failure_result(
        self,
        *,
        turn: AgentTurn,
        error: AppError,
        traces: list[CapabilityTrace],
    ) -> AgentTurnResult:
        """构造统一失败结果。

        参数：
        1. `turn`：当前轮输入对象。
        2. `error`：结构化错误对象。
        3. `traces`：当前轮轨迹列表。

        返回值：
        1. `action=fail` 的统一结果。
        """

        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            action="fail",
            reply_text=f"抱歉，这一轮处理失败了：{error.message}",
            capability_traces=traces,
            meta={"error": error.to_dict()},
        )
