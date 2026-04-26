"""agent-core 对外统一门面。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict

from agent_core.camera import CameraGateway
from agent_core.context import AgentSessionStore, AgentTurn, AgentTurnResult, MessageContext, generate_id
from agent_core.context.models import CapabilityTrace, MediaAssetRef
from agent_core.runtime import AgentLoopRunner
from agent_core.tools import ToolGateway, ToolRegistry
from backend_task_core import TaskEvent
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
        system_prompt: str = "",
    ) -> None:
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._runner = runner
        self._system_prompt = system_prompt
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

        from openaiglasses.server import build_default_agent_facade

        return build_default_agent_facade(
            settings=settings,
            device_state_reader=device_state_reader,
        )

    def handle_turn(
        self,
        turn: AgentTurn,
        *,
        progress_callback: Callable[[str], None] | None = None,
        reply_text_delta_callback: Callable[[str], None] | None = None,
    ) -> AgentTurnResult:
        """处理一轮 Agent 输入。

        主要逻辑：
        1. 获取或创建会话。
        2. 保存当前轮资产与派生结果。
        3. 先写入用户消息，再执行 Agent Loop。
        4. 把最终回复、轨迹和失败信息回写到上下文。

        参数：
        1. `turn`：当前轮输入对象。
        2. `progress_callback`：中间播报回调，用于长耗时工具前的即时反馈。
        3. `reply_text_delta_callback`：最终回复的文本增量回调，用于流式播报。

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
            result = self._runner.run_turn(
                session=session,
                turn=turn,
                progress_callback=progress_callback,
                reply_text_delta_callback=reply_text_delta_callback,
            )
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

    def bind_camera_gateway(self, camera_gateway: CameraGateway) -> None:
        """补绑真实相机网关。

        主要逻辑：
        1. 默认 `AgentFacade` 在 `VoiceRuntime` 初始化时先行构建。
        2. 等 `ControlRuntime` 就绪后，再把设备侧相机能力绑定进 `ToolRegistry`。

        参数：
        1. `camera_gateway`：真实相机抓拍网关。
        """

        self._tool_registry.bind_camera_gateway(camera_gateway)

    def bind_device_state_reader(self, device_state_reader) -> None:
        """补绑真实设备运行态读取函数。"""

        self._tool_registry.bind_device_state_reader(device_state_reader)

    def bind_device_group_context_factory(self, factory) -> None:
        """补绑 DeviceGroupContext 工厂。"""

        self._tool_registry.bind_device_group_context_factory(factory)

    def bind_task_event_listener(self, listener: Callable[[TaskEvent], None]) -> None:
        """补绑任务事件监听器。

        主要逻辑：
        1. 让 `backend-task-core` 的任务事件能回流到外部运行时。
        2. 当前主要用于把后台任务事件交给语音运行时播报。
        """

        self._tool_registry.get_task_gateway().subscribe_events(listener)

    def shutdown(self) -> None:
        """关闭门面内部后台资源。"""

        self._tool_registry.get_task_gateway().shutdown()

    def get_task_gateway(self):
        """返回内部任务网关。

        主要逻辑：
        1. 对外暴露 `backend-task-core` 的统一访问入口。
        2. 供调试接口或联调脚本在不经过完整 Agent Loop 时创建后台任务。
        """

        return self._tool_registry.get_task_gateway()

    def get_tool_registry(self):
        """返回内部工具注册表。"""

        return self._tool_registry

    def get_session_store(self) -> AgentSessionStore:
        """返回内部会话存储。

        主要逻辑：
        1. 供 SDK 设备组上下文把旁路 MCP 调用轨迹写回同一会话。
        2. 避免业务代码直接接触 agent-core 私有字段。
        """

        return self._session_store

    def _persist_result(self, *, turn: AgentTurn, result: AgentTurnResult) -> AgentTurnResult:
        """把运行结果统一写回会话上下文。"""

        assistant_message_id = generate_id("msg")
        assistant_asset_ids = self._session_store.save_assets(
            session_id=turn.session_id,
            assets=result.meta.get("asset_refs", []),
        )
        assistant_artifact_ids = self._session_store.save_artifacts(
            session_id=turn.session_id,
            artifacts=result.meta.get("derived_artifacts", []),
        )
        assistant_task_ids = self._session_store.save_task_refs(
            session_id=turn.session_id,
            task_refs=result.meta.get("task_refs", []),
        )
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
                kind="assistant_reply",
                text=result.reply_text,
                asset_refs=assistant_asset_ids,
                derived_refs=assistant_artifact_ids,
                task_refs=assistant_task_ids,
                meta={
                    "turn_id": turn.turn_id,
                    "capability_trace_ids": [trace.trace_id for trace in result.capability_traces],
                    **({"error": result.error} if result.error is not None else {}),
                },
            ),
        )

        session = self._session_store.get_session(turn.session_id)
        if session is not None:
            model_request = result.meta.get("model_request")
            if model_request is not None:
                session.dialog_state.meta["last_model_request"] = model_request
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

    def get_session_debug_snapshot(self, session_id: str) -> dict[str, object] | None:
        """返回可用于联调与回归结果落盘的会话快照。"""

        session = self._session_store.get_session(session_id)
        if session is None:
            return None
        return {
            "session_id": session.session_id,
            "device_id": session.device_id,
            "created_at_ms": session.created_at_ms,
            "updated_at_ms": session.updated_at_ms,
            "dialog_state": {
                "pending_question": session.dialog_state.pending_question,
                "missing_slots": list(session.dialog_state.missing_slots),
                "meta": dict(session.dialog_state.meta),
            },
            "model_request": session.dialog_state.meta.get("last_model_request"),
            "messages": self._build_debug_messages(session),
            "assets": {asset_id: asdict(asset) for asset_id, asset in session.assets.items()},
            "artifacts": {artifact_id: asdict(artifact) for artifact_id, artifact in session.artifacts.items()},
            "tasks": {task_id: asdict(task) for task_id, task in session.tasks.items()},
            "capability_traces": [asdict(trace) for trace in session.capability_traces],
        }

    def _build_debug_messages(self, session) -> list[dict[str, object]]:
        """构造用于联调导出的完整消息列表。

        主要逻辑：
        1. 若当前门面持有系统提示词，则在消息首位补一条 `system` 消息。
        2. 后续顺序保留会话内真实写入的 user/assistant 消息。

        参数：
        1. `session`：当前会话对象。

        返回值：
        1. 适合直接写入调试快照或 `result.json` 的完整消息列表。
        """

        messages: list[dict[str, object]] = []
        if self._system_prompt:
            messages.append(
                {
                    "message_id": "system_prompt",
                    "session_id": session.session_id,
                    "role": "system",
                    "kind": "system_prompt",
                    "text": self._system_prompt,
                    "asset_refs": [],
                    "derived_refs": [],
                    "task_refs": [],
                    "meta": {"source": "settings.voice_system_prompt"},
                    "created_at_ms": session.created_at_ms,
                }
            )
        messages.extend(asdict(message) for message in session.messages)
        return messages

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
        1. 统一失败结果。
        """

        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            reply_text=f"抱歉，这一轮处理失败了：{error.message}",
            capability_traces=traces,
            error=error.to_dict(),
            meta={"error": error.to_dict()},
        )
