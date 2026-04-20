"""agent-core 运行循环实现。"""

from __future__ import annotations

import asyncio
import base64
from abc import ABC, abstractmethod
from collections.abc import Callable

from agent_core.context.models import AgentSession, AgentTurn, AgentTurnResult
from agent_core.context.session_store import AgentSessionStore
from agent_core.tools import AgentToolContext, ToolGateway, ToolRegistry
from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug


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
    ) -> None:
        self._settings = settings
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._tool_gateway = tool_gateway
        self._logger = get_logger("server.agent.runner")

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
        3. 若模型选择了 `capture_photo`，则在拍照完成后中止旧 loop，
           直接进入主链路图片解读，避免再走一轮无意义的工具总结回复。

        参数：
        1. `session`：当前会话对象。
        2. `turn`：当前轮输入对象。
        3. `progress_callback`：中间播报回调，适合在长耗时工具前给用户一句反馈。
        4. `reply_text_delta_callback`：最终回复文本增量回调，便于调用方做流式 TTS。

        返回值：
        1. `AgentTurnResult`。
        """

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
            if progress_callback is None and reply_text_delta_callback is None:
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

            stream_result = self._run_async_with_thread_event_loop(
                self._run_streamed_turn(
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

        return self._attach_capability_outputs(
            result=stream_result,
            context=tool_context,
        )

    async def _run_streamed_turn(
        self,
        *,
        agent,
        run_input: list[dict[str, str]],
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
        2. 若命中 `capture_photo`，在拍照后中止原 loop，改为直接进入图片解读主链路。
        3. 若未命中拍照，则回落到 SDK 自身给出的最终回复文本。

        返回值：
        1. 完整 `AgentTurnResult`。
        """

        from agents import Runner
        from agents.items import ItemHelpers, MessageOutputItem

        run_result = Runner.run_streamed(
            agent,
            run_input,
            context=tool_context,
            max_turns=6,
            run_config=run_config,
            conversation_id=turn.session_id,
        )

        capture_call_id: str | None = None
        existing_image_asset_ids = self._collect_image_asset_ids(tool_context=tool_context, session=session)
        progress_sent = False

        event_stream = run_result.stream_events()
        try:
            async for event in event_stream:
                if getattr(event, "type", "") != "run_item_stream_event":
                    continue

                if event.name == "message_output_created" and isinstance(event.item, MessageOutputItem):
                    message_text = ItemHelpers.text_message_output(event.item).strip()
                    if message_text and not progress_sent and progress_callback is not None:
                        progress_callback(message_text)
                        progress_sent = True
                    continue

                if event.name == "tool_called":
                    raw_item = getattr(event.item, "raw_item", None)
                    tool_name = getattr(raw_item, "name", "")
                    if tool_name == "capture_photo":
                        capture_call_id = getattr(raw_item, "call_id", None)
                        if not progress_sent and progress_callback is not None:
                            progress_callback("好的，你保持别动，我拍一张帮你看。")
                            progress_sent = True
                        image_asset = await self._wait_for_new_image_asset(
                            tool_context=tool_context,
                            session=session,
                            excluded_asset_ids=existing_image_asset_ids,
                            timeout_seconds=10.0,
                        )
                        if image_asset is not None:
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
                    continue

                if event.name == "tool_output" and capture_call_id is not None:
                    raw_item = getattr(event.item, "raw_item", None)
                    if getattr(raw_item, "call_id", None) == capture_call_id:
                        run_result.cancel()
                        image_asset = await self._wait_for_new_image_asset(
                            tool_context=tool_context,
                            session=session,
                            excluded_asset_ids=existing_image_asset_ids,
                            timeout_seconds=1.0,
                        )
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
        finally:
            aclose = getattr(event_stream, "aclose", None)
            if callable(aclose):
                await aclose()

        reply_text = self._extract_reply_text(run_result.final_output)
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

    async def _wait_for_new_image_asset(
        self,
        *,
        tool_context: AgentToolContext,
        session: AgentSession,
        excluded_asset_ids: set[str],
        timeout_seconds: float,
    ):
        """等待 `capture_photo` 产出本次新抓拍图片。

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
            timeout=self._settings.voice_model_timeout_ms / 1000,
        )

        reply_parts: list[str] = []
        for chunk in completion:
            text_delta = self._extract_stream_text_delta(chunk)
            if not text_delta:
                continue
            reply_parts.append(text_delta)
            if reply_text_delta_callback is not None:
                reply_text_delta_callback(text_delta)
        reply_text = "".join(reply_parts).strip()
        log_debug(
            self._logger,
            f"主链路图片解读完成: reply_length={len(reply_text)}",
            LogContext(device_id=turn.device_id, session_id=turn.session_id, message_id=turn.turn_id),
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
    def _run_async_with_thread_event_loop(awaitable):
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
            return target_loop.run_until_complete(awaitable)
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
            "如果需要拍照再回答，可以先用一句很短的话安抚用户，然后立即调用拍照工具。\n"
            "必要时可以调用已提供的工具。\n"
            "不要输出代码块。\n"
        )

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
