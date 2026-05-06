"""agent-core 对外统一门面。"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import asdict

from agent_core.camera import CameraGateway
from agent_core.camera.utterance_photo import UtterancePhotoRecord
from agent_core.context import AgentSessionStore, AgentTurn, AgentTurnResult, MessageContext, generate_id
from agent_core.context.models import CapabilityTrace, MediaAssetRef
from agent_core.modality import AgentInputPlanner, ImageInputPlan, ImageInputPolicy, ModelCapability
from agent_core.runtime import AgentLoopRunner, NativeAudioReplyResult, PreparedNativeAudioReply
from agent_core.streaming import TurnCoordinator
from agent_core.tools import ToolGateway, ToolRegistry
from backend_task_core import TaskEvent
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug, log_error

_MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}


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
        settings: ServerSettings | None = None,
        system_prompt: str = "",
        turn_coordinator: TurnCoordinator | None = None,
        image_policy: ImageInputPolicy = ImageInputPolicy.DIRECT_WHEN_SUPPORTED,
    ) -> None:
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._runner = runner
        self._settings = settings or ServerSettings()
        self._system_prompt = system_prompt
        self._turn_coordinator = turn_coordinator or TurnCoordinator()
        self._image_policy = image_policy
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
        reply_audio_chunk_callback: Callable[[object], None] | None = None,
        native_audio_reply_runner: Callable[..., object] | None = None,
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
        stream_id = str(turn.meta.get("stream_id") or "")
        start_event = self._turn_coordinator.start_turn(
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            stream_id=stream_id,
            payload={"source": turn.source},
        )
        turn.meta["generation_id"] = start_event.generation_id
        turn.meta["turn_started_event_id"] = start_event.event_id
        turn.asset_refs.extend(self._consume_ready_utterance_photos(turn=turn))
        asset_ids = self._session_store.save_assets(session_id=turn.session_id, assets=turn.asset_refs)
        artifact_ids = self._session_store.save_artifacts(session_id=turn.session_id, artifacts=turn.derived_artifacts)
        self._turn_coordinator.emit(
            event_type="input.assets.saved",
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            generation_id=start_event.generation_id,
            stream_id=stream_id,
            causation_id=start_event.event_id,
            payload={"asset_ids": asset_ids, "artifact_ids": artifact_ids},
        )

        user_message_id = generate_id("msg")
        self._session_store.append_message(
            session_id=turn.session_id,
            message=MessageContext(
                message_id=user_message_id,
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
                reply_audio_chunk_callback=reply_audio_chunk_callback,
                native_audio_reply_runner=native_audio_reply_runner,
            )
        except AppError as exc:
            log_error(
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
            log_error(
                self._logger,
                f"agent-core 返回非结构化失败: error={error.to_dict()} input_text={turn.input_text!r}",
                LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
            )
            result = self._build_failure_result(turn=turn, error=error, traces=[])

        user_text_override = str(result.meta.get("user_text_override") or "").strip()
        if user_text_override:
            self._session_store.update_message_text(
                session_id=turn.session_id,
                message_id=user_message_id,
                text=user_text_override,
            )

        if result.error is None:
            self._turn_coordinator.emit(
                event_type="text.final",
                session_id=turn.session_id,
                device_id=turn.device_id,
                turn_id=turn.turn_id,
                generation_id=start_event.generation_id,
                stream_id=stream_id,
                payload={
                    "reply_length": len(result.reply_text),
                    "user_text_override": bool(user_text_override),
                },
            )
            self._turn_coordinator.finish_turn(
                session_id=turn.session_id,
                device_id=turn.device_id,
                turn_id=turn.turn_id,
                generation_id=start_event.generation_id,
                stream_id=stream_id,
            )
        else:
            self._turn_coordinator.fail_turn(
                session_id=turn.session_id,
                device_id=turn.device_id,
                turn_id=turn.turn_id,
                generation_id=start_event.generation_id,
                stream_id=stream_id,
                payload={"error": result.error},
            )
        result.meta["generation_id"] = start_event.generation_id
        result.meta["turn_events"] = self._turn_coordinator.snapshot(turn_id=turn.turn_id)
        result = self._persist_result(turn=turn, result=result)
        return result

    def prepare_native_audio_turn(
        self,
        turn: AgentTurn,
        *,
        progress_callback: Callable[[str], None] | None = None,
    ) -> PreparedNativeAudioReply:
        """提前准备原生音频 Realtime 轮次。

        主要逻辑：
        1. 获取或创建当前 Agent 会话。
        2. 委托 runner 构造 instructions、工具 schema 和工具处理器。
        3. 不保存用户消息、不调用模型，用于语音段开始时提前建立 Omni WebSocket。

        参数：
            turn: 当前语音轮次占位对象。
            progress_callback: 工具前置播报回调。

        返回值：
            `PreparedNativeAudioReply`，供 voice-runtime 逐帧转发音频。
        """

        session = self._session_store.get_or_create_session(
            session_id=turn.session_id,
            device_id=turn.device_id,
        )
        prepare = getattr(self._runner, "prepare_native_audio_reply", None)
        if not callable(prepare):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "当前 Agent runner 不支持原生音频 Realtime 预备运行态",
            )
        return prepare(session=session, turn=turn, progress_callback=progress_callback)

    def consume_ready_utterance_photos(self, turn: AgentTurn) -> list[MediaAssetRef]:
        """消费当前语音轮次已经完成的自动抓拍照片。

        主要逻辑：
        1. 复用统一图片策略，决定图片是直接传给模型还是延后由工具处理。
        2. 将照片保存为当前轮资产，供 Realtime 提交和后续会话记录使用。

        参数：
            turn: 当前语音轮次。

        返回值：
            可挂接到当前用户输入的图片资产列表。
        """

        return self._consume_ready_utterance_photos(turn=turn)

    def complete_prepared_native_audio_turn(
        self,
        *,
        turn: AgentTurn,
        prepared: PreparedNativeAudioReply,
        native_result: NativeAudioReplyResult,
    ) -> AgentTurnResult:
        """把已完成的原生音频 Realtime 结果写回 Agent-Core 会话。

        主要逻辑：
        1. 保存当前轮音频、图片资产和用户消息。
        2. 使用 Realtime 返回的文本转写更新用户消息。
        3. 保存助手回复、能力轨迹和模型请求摘要。

        参数：
            turn: 当前语音轮次。
            prepared: 语音段开始时准备好的运行态。
            native_result: Omni Realtime 完成后返回的文本、转写和响应编号。

        返回值：
            标准 `AgentTurnResult`。
        """

        self._session_store.get_or_create_session(session_id=turn.session_id, device_id=turn.device_id)
        stream_id = str(turn.meta.get("stream_id") or "")
        start_event = self._turn_coordinator.start_turn(
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            stream_id=stream_id,
            payload={"source": turn.source},
        )
        turn.meta["generation_id"] = start_event.generation_id
        turn.meta["turn_started_event_id"] = start_event.event_id
        turn.asset_refs.extend(self._consume_ready_utterance_photos(turn=turn))
        asset_ids = self._session_store.save_assets(session_id=turn.session_id, assets=turn.asset_refs)
        artifact_ids = self._session_store.save_artifacts(session_id=turn.session_id, artifacts=turn.derived_artifacts)
        self._turn_coordinator.emit(
            event_type="input.assets.saved",
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            generation_id=start_event.generation_id,
            stream_id=stream_id,
            causation_id=start_event.event_id,
            payload={"asset_ids": asset_ids, "artifact_ids": artifact_ids},
        )

        user_message_id = generate_id("msg")
        user_text = native_result.transcript.strip() or turn.input_text
        self._session_store.append_message(
            session_id=turn.session_id,
            message=MessageContext(
                message_id=user_message_id,
                session_id=turn.session_id,
                role="user",
                kind="audio_input",
                text=user_text,
                asset_refs=asset_ids,
                derived_refs=artifact_ids,
                meta={
                    "turn_id": turn.turn_id,
                    "source": turn.source,
                    **turn.meta,
                },
            ),
        )

        assistant_text = native_result.assistant_text.strip()
        if not assistant_text:
            result = self._build_failure_result(
                turn=turn,
                error=build_error(ErrorCode.INTERNAL_ERROR, "agent-core 原生音频回复链路返回了空文本"),
                traces=prepared.runtime.capability_traces,
            )
        else:
            model_request = dict(prepared.model_request)
            audio_bytes = next(
                (asset.bytes for asset in turn.asset_refs if asset.asset_type == "audio"),
                model_request.get("audio_bytes"),
            )
            image_count = len([asset for asset in turn.asset_refs if asset.asset_type == "image"])
            model_request["audio_bytes"] = audio_bytes
            model_request["image_count"] = image_count
            messages = model_request.get("messages")
            if isinstance(messages, list):
                for message in messages:
                    if not isinstance(message, dict) or message.get("role") != "user":
                        continue
                    content = message.get("content")
                    if not isinstance(content, list):
                        continue
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        if item.get("type") == "input_audio_stream":
                            item["audio_bytes"] = audio_bytes
                        if item.get("type") == "input_image_batch":
                            item["image_count"] = image_count
            log_debug(
                self._logger,
                "agent-core 模型请求完整 messages",
                LogContext(
                    device_id=turn.device_id,
                    session_id=turn.session_id,
                    message_id=turn.turn_id,
                    fields={
                        "stage": "native_audio_realtime_completed",
                        "model": model_request.get("model"),
                        "runner": model_request.get("runner"),
                        "message_count": len(messages) if isinstance(messages, list) else 0,
                        "messages": messages if isinstance(messages, list) else [],
                    },
                ),
            )
            result = AgentTurnResult(
                turn_id=turn.turn_id,
                session_id=turn.session_id,
                device_id=turn.device_id,
                reply_text=assistant_text,
                capability_traces=prepared.runtime.capability_traces,
                meta={
                    "model_request": model_request,
                    "user_text_override": native_result.transcript.strip(),
                    "native_audio_response_id": native_result.response_id,
                    "asset_refs": list(prepared.runtime.tool_context.emitted_assets),
                    "derived_artifacts": list(prepared.runtime.tool_context.emitted_artifacts),
                    "task_refs": list(prepared.runtime.tool_context.emitted_tasks),
                    "turn_meta": dict(prepared.runtime.tool_context.turn_meta),
                    **(native_result.meta or {}),
                },
            )
        result.meta["user_message_id"] = user_message_id

        if result.error is None:
            self._turn_coordinator.emit(
                event_type="text.final",
                session_id=turn.session_id,
                device_id=turn.device_id,
                turn_id=turn.turn_id,
                generation_id=start_event.generation_id,
                stream_id=stream_id,
                payload={
                    "reply_length": len(result.reply_text),
                    "user_text_override": bool(native_result.transcript.strip()),
                },
            )
            self._turn_coordinator.finish_turn(
                session_id=turn.session_id,
                device_id=turn.device_id,
                turn_id=turn.turn_id,
                generation_id=start_event.generation_id,
                stream_id=stream_id,
            )
        else:
            self._turn_coordinator.fail_turn(
                session_id=turn.session_id,
                device_id=turn.device_id,
                turn_id=turn.turn_id,
                generation_id=start_event.generation_id,
                stream_id=stream_id,
                payload={"error": result.error},
            )
        result.meta["generation_id"] = start_event.generation_id
        result.meta["turn_events"] = self._turn_coordinator.snapshot(turn_id=turn.turn_id)
        return self._persist_result(turn=turn, result=result)

    def bind_camera_gateway(self, camera_gateway: CameraGateway) -> None:
        """补绑真实相机网关。

        主要逻辑：
        1. 默认 `AgentFacade` 在 `VoiceRuntime` 初始化时先行构建。
        2. 等 `ControlRuntime` 就绪后，再把设备侧相机能力绑定进 `ToolRegistry`。

        参数：
        1. `camera_gateway`：真实相机抓拍网关。
        """

        self._tool_registry.bind_camera_gateway(camera_gateway)

    def _consume_ready_utterance_photos(self, *, turn: AgentTurn) -> list[MediaAssetRef]:
        """把已就绪自动照片转换成当前用户输入资产。

        主要逻辑：
        1. 从 `UtterancePhotoStore` 一次性取出当前会话未使用的自动照片。
        2. 将图片字节落盘到本轮会话目录，生成 `MediaAssetRef`。
        3. 返回的资产会挂接到当前用户消息，runner 会把它们装入多模态 user message。

        参数：
        1. `turn`：当前 Agent 输入轮次。

        返回值：
        1. 当前轮可随用户文本发送给多模态模型的图片资产列表。

        异常情况：
        1. 自动照片是低延迟辅助输入，落盘失败时只记录错误并继续文本链路。
        """

        store = self._tool_registry.get_utterance_photo_store()
        records = store.consume_ready_photos(session_id=turn.session_id, device_id=turn.device_id)
        if not records:
            return []

        assets: list[MediaAssetRef] = []
        for record in records:
            try:
                assets.append(self._record_to_image_asset(turn=turn, record=record))
            except Exception as exc:  # noqa: BLE001 - 自动照片失败不应打断语音问答主链路
                log_error(
                    self._logger,
                    f"自动照片写入当前输入失败: reason={exc!r} segment_id={record.segment_id}",
                    LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
                )
        if assets:
            image_plan = self._plan_image_assets(assets)
            turn.meta["auto_utterance_photo_asset_ids"] = [asset.asset_id for asset in assets]
            turn.meta["direct_image_asset_ids"] = [asset.asset_id for asset in image_plan.direct_assets]
            turn.meta["deferred_image_asset_ids"] = [asset.asset_id for asset in image_plan.deferred_assets]
            turn.meta["image_input_policy"] = image_plan.policy.value
            turn.meta["image_model_capability"] = {
                "model_name": image_plan.model_capability.model_name,
                "supports_image_input": image_plan.model_capability.supports_image_input,
            }
            log_debug(
                self._logger,
                "自动照片已装入当前用户输入",
                LogContext(
                    device_id=turn.device_id,
                    session_id=turn.session_id,
                    message_id=turn.turn_id,
                    fields={
                        "asset_ids": [asset.asset_id for asset in assets],
                        "direct_asset_ids": [asset.asset_id for asset in image_plan.direct_assets],
                        "deferred_asset_ids": [asset.asset_id for asset in image_plan.deferred_assets],
                        "policy": image_plan.policy.value,
                        "count": len(assets),
                    },
                ),
            )
        return assets

    def _plan_image_assets(self, assets: list[MediaAssetRef]) -> ImageInputPlan:
        """规划当前轮图片资产的直传或后置处理方式。

        参数：
        1. `assets`：当前轮自动抓拍得到的图片资产。

        返回值：
        1. `ImageInputPlan`，包含直传图片和后置图片列表。

        异常情况：
        1. 只基于配置和内存对象规划，不主动抛出业务异常。
        """

        reply_mode = "omni_realtime" if self._settings.effective_voice_server_mode() == "omni_server" else "agent_tts"
        model_name = (
            self._settings.voice_omni_realtime_model_name
            if reply_mode == "omni_realtime"
            else self._settings.agent_model_name
        )
        planner = AgentInputPlanner(
            model_capability=ModelCapability.from_model_name(model_name),
            image_policy=self._image_policy,
        )
        return planner.plan_images([asset for asset in assets if asset.asset_type == "image"])

    def _record_to_image_asset(self, *, turn: AgentTurn, record: UtterancePhotoRecord) -> MediaAssetRef:
        """将一条自动照片记录保存为会话图片资产。

        参数：
        1. `turn`：当前 Agent 输入轮次。
        2. `record`：已就绪且已标记消费的自动照片记录。

        返回值：
        1. 可挂接到当前用户消息的 `MediaAssetRef`。

        异常情况：
        1. 若记录缺少图片结果或文件系统写入失败，会向上抛出异常，由调用方降级处理。
        """

        if record.result is None:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "自动照片记录已消费但没有图片结果",
                details={"segment_id": record.segment_id},
            )
        asset_id = generate_id("asset")
        capture_dir = os.path.join(
            self._settings.voice_runs_root,
            turn.session_id,
            "image",
            "utterance",
        )
        os.makedirs(capture_dir, exist_ok=True)
        extension = _MIME_EXTENSION_MAP.get(record.result.mime_type.lower(), ".bin")
        storage_uri = os.path.join(capture_dir, f"{asset_id}{extension}")
        with open(storage_uri, "wb") as file:
            file.write(record.result.image_bytes)

        return MediaAssetRef(
            asset_id=asset_id,
            session_id=turn.session_id,
            asset_type="image",
            storage_uri=storage_uri,
            mime_type=record.result.mime_type,
            codec=record.result.codec,
            width=record.result.width,
            height=record.result.height,
            bytes=len(record.result.image_bytes),
            source_stream_id=record.stream_id or None,
        )

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

    def get_skill_runtime(self):
        """返回内部 Skill Runtime。"""

        return self._tool_registry.get_skill_runtime()

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
