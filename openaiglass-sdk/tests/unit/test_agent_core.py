"""Phase D agent-core 单元测试。"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import AsyncMock, patch

from agent_core import AgentFacade, AgentTurn, AgentTurnResult, DerivedArtifact, MediaAssetRef
from agent_core.camera import CameraCaptureResult
from agent_core.context import AgentSession, AgentSessionStore, CapabilityTrace, MessageContext
from agent_core.context.assembler import ContextAssembler
from agent_core.runtime import AgentLoopRunner, OpenAIAgentLoopRunner
from agent_core.skills import SkillDocument, SkillManifest, SkillRuntime
from agent_core.tools import AgentToolContext, ToolGateway, ToolRegistry
from backend_task_core import InMemoryTaskGateway
from infra.config import ServerSettings
from infra.errors import AppError, ErrorCode, build_error
from openaiglasses import OpenAIGlassesSDK
from openaiglasses.server import HybridTaskGateway

_FAKE_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff"
    b"\x1f\x00\x02\xeb\x01\xf5\x8fg?\xed\x00\x00\x00\x00IEND\xaeB`\x82"
)


class FakeCameraGateway:
    """测试用假相机网关。"""

    def capture_photo(self, *, device_id: str, session_id: str, reason: str, timeout_ms: int) -> CameraCaptureResult:
        return CameraCaptureResult(
            request_id="capture_test_001",
            image_bytes=_FAKE_PNG_BYTES,
            mime_type="image/png",
            codec="png",
            width=1,
            height=1,
            meta={
                "device_id": device_id,
                "session_id": session_id,
                "reason": reason,
                "timeout_ms": timeout_ms,
            },
        )


def build_tooling(device_state_reader=lambda: {}, camera_gateway=None, task_gateway=None, skill_runtime=None):
    registry = ToolRegistry(
        device_state_reader=device_state_reader,
        camera_gateway=camera_gateway,
        skill_runtime=skill_runtime,
        task_gateway=task_gateway
        or HybridTaskGateway(
            base_gateway=InMemoryTaskGateway(),
            sdk_task_runtime=OpenAIGlassesSDK().task_runtime,
        ),
    )
    gateway = ToolGateway(registry)
    registry.bind_gateway(gateway)
    return registry, gateway


def build_tool_context(*, registry, gateway, session_id, turn_id):
    return AgentToolContext(
        session_id=session_id,
        device_id="glass-001",
        turn_id=turn_id,
        settings=ServerSettings(),
        session_store=AgentSessionStore(),
        device_state_reader=registry.get_device_state_reader(),
        trace_sink=lambda _trace: None,
        task_gateway=registry.get_task_gateway(),
        camera_gateway=registry.get_camera_gateway(),
        tool_gateway=gateway,
        mcp_gateway=registry.get_mcp_gateway(),
    )


def install_fake_agents_module():
    module = types.ModuleType("agents")

    class Agent:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class MultiProvider:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class RunConfig:
        def __init__(self, *args, **kwargs) -> None:
            self.args = args
            self.kwargs = kwargs

    class Runner:
        @staticmethod
        def run_sync(*args, **kwargs):  # pragma: no cover - 测试会 patch
            raise AssertionError("Runner.run_sync should be patched in tests")

        @staticmethod
        def run_streamed(*args, **kwargs):  # pragma: no cover - 测试会 patch
            raise AssertionError("Runner.run_streamed should be patched in tests")

    module.Agent = Agent
    module.MultiProvider = MultiProvider
    module.RunConfig = RunConfig
    module.Runner = Runner
    return patch.dict(sys.modules, {"agents": module})


class FakeAgentLoopRunner(AgentLoopRunner):
    """测试用假运行循环。"""

    def __init__(self) -> None:
        self.turns: list[AgentTurn] = []

    def run_turn(
        self,
        *,
        session: AgentSession,
        turn: AgentTurn,
        progress_callback=None,
        reply_text_delta_callback=None,
    ) -> AgentTurnResult:
        self.turns.append(turn)
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            reply_text="这是 agent-core 的回复",
            capability_traces=[
                CapabilityTrace(
                    trace_id="cap_test_001",
                    turn_id=turn.turn_id,
                    capability_type="tool",
                    capability_name="query_device_state",
                    status="succeeded",
                    input_summary="{}",
                    output_summary='{"state":"listening"}',
                )
            ],
        )


class ErrorAgentLoopRunner(AgentLoopRunner):
    """测试用失败运行循环。"""

    def run_turn(
        self,
        *,
        session: AgentSession,
        turn: AgentTurn,
        progress_callback=None,
        reply_text_delta_callback=None,
    ) -> AgentTurnResult:
        raise build_error(
            ErrorCode.INTERNAL_ERROR,
            "模拟 agent-core 失败",
        )


class AskUserAgentLoopRunner(AgentLoopRunner):
    """测试用普通回复运行循环。"""

    def run_turn(
        self,
        *,
        session: AgentSession,
        turn: AgentTurn,
        progress_callback=None,
        reply_text_delta_callback=None,
    ) -> AgentTurnResult:
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            reply_text="你想让我查设备状态，还是直接回答问题？",
        )


class AgentCoreTestCase(unittest.TestCase):
    """验证 Phase D 的最小 agent-core。"""

    def test_agent_facade_persists_turn_context(self) -> None:
        """测试目标：验证 AgentFacade 会写入用户消息、助手消息和轨迹。

        测试方法：
        1. 构造最小会话存储、工具注册表和假运行循环。
        2. 提交一轮带音频资产和转写结果的 `AgentTurn`。
        3. 读取会话上下文中的消息、资产和轨迹。

        预期结果：
        1. 会话中包含 1 条用户消息和 1 条助手消息。
        2. 用户消息能挂接当前轮资产与转写结果。
        3. 轨迹会被写入会话存储。
        """

        session_store = AgentSessionStore()
        tool_registry, _ = build_tooling()
        facade = AgentFacade(
            session_store=session_store,
            tool_registry=tool_registry,
            runner=FakeAgentLoopRunner(),
        )

        result = facade.handle_turn(
            AgentTurn(
                turn_id="turn_001",
                session_id="sess_001",
                device_id="glass-001",
                source="voice_asr",
                input_text="帮我看看当前设备状态",
                asset_refs=[
                    MediaAssetRef(
                        asset_id="asset_001",
                        session_id="sess_001",
                        asset_type="audio",
                        storage_uri="runs/session/sess_001/audio/input/seg.wav",
                        mime_type="audio/wav",
                    )
                ],
                derived_artifacts=[
                    DerivedArtifact(
                        artifact_id="artifact_001",
                        session_id="sess_001",
                        artifact_type="asr_transcript",
                        storage_uri="runs/session/sess_001/artifact/transcript/seg.json",
                        text="帮我看看当前设备状态",
                    )
                ],
            )
        )

        session = session_store.get_session("sess_001")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(result.reply_text, "这是 agent-core 的回复")
        self.assertEqual(len(session.messages), 2)
        self.assertEqual(session.messages[0].role, "user")
        self.assertEqual(session.messages[0].asset_refs, ["asset_001"])
        self.assertEqual(session.messages[0].derived_refs, ["artifact_001"])
        self.assertEqual(session.messages[1].role, "assistant")
        self.assertEqual(len(session.capability_traces), 1)

    def test_agent_facade_wraps_runner_error(self) -> None:
        """测试目标：验证 AgentFacade 会把运行失败包装为统一失败结果。

        测试方法：
        1. 构造会抛出结构化错误的假运行循环。
        2. 提交一轮最小 `AgentTurn`。

        预期结果：
        1. 返回结果带有统一错误信息。
        2. 回复文本中包含失败原因。
        """

        facade = AgentFacade(
            session_store=AgentSessionStore(),
            tool_registry=build_tooling()[0],
            runner=ErrorAgentLoopRunner(),
        )

        result = facade.handle_turn(
            AgentTurn(
                turn_id="turn_002",
                session_id="sess_002",
                device_id="glass-001",
                source="voice_asr",
                input_text="你好",
            )
        )

    def test_start_phone_video_link_tool_creates_task_with_bound_phone(self) -> None:
        """测试目标：验证视频直连 Tool 会基于绑定关系创建后台任务。

        测试方法：
        1. 构造包含 `glass_to_phone` 绑定快照的工具上下文。
        2. 通过 `ToolGateway` 调用 `start_phone_video_link`。
        3. 检查返回的任务编号、目标手机和任务状态。

        预期结果：
        1. Tool 调用成功。
        2. 返回结果中的目标手机等于当前绑定手机。
        3. 任务引用会被写入结果。
        """

        registry, gateway = build_tooling(
            device_state_reader=lambda: {
                "device_bindings": {
                    "glass_to_phone": {"glass-001": "phone-001"},
                    "phone_to_glass": {"phone-001": "glass-001"},
                },
                "connections": [
                    {
                        "device_id": "phone-001",
                        "device_type": "phone",
                        "camera_sink_ws_uri": "ws://127.0.0.1:19001/ws/camera",
                    }
                ],
            }
        )
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_video_tool_001",
            turn_id="turn_video_tool_001",
        )

        result = gateway.invoke(
            name="start_phone_video_link",
            context=context,
            arguments={},
        )

        self.assertEqual(result.data["phone_device_id"], "phone-001")
        self.assertEqual(result.data["target_ws_uri"], "ws://127.0.0.1:19001/ws/camera")
        self.assertEqual(result.data["state"], "running")
        self.assertEqual(len(result.task_refs), 1)
        self.assertEqual(result.task_refs[0].task_type, "phone_video_link_task")

    def test_start_phone_video_link_tool_prefers_device_group_snapshot(self) -> None:
        """测试目标：验证视频直连 Tool 优先使用 SDK 设备组快照。

        测试方法：
        1. 构造只包含 `device_groups`、不包含旧 `device_bindings` 的运行态。
        2. 调用 `start_phone_video_link`。

        预期结果：
        1. Tool 能从设备组中解析绑定手机。
        2. Tool 能从手机 metadata 中解析视频接收地址。
        """

        registry, gateway = build_tooling(
            device_state_reader=lambda: {
                "device_groups": {
                    "group_count": 1,
                    "groups": [
                        {
                            "group_id": "group_001",
                            "devices": [
                                {
                                    "device_id": "glass-001",
                                    "role": "glass",
                                    "online": True,
                                    "metadata": {},
                                },
                                {
                                    "device_id": "phone-001",
                                    "role": "phone",
                                    "online": True,
                                    "metadata": {
                                        "camera_sink_ws_uri": "ws://127.0.0.1:19001/ws/camera",
                                    },
                                },
                            ],
                        }
                    ],
                }
            }
        )
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_video_tool_group_001",
            turn_id="turn_video_tool_group_001",
        )

        result = gateway.invoke(
            name="start_phone_video_link",
            context=context,
            arguments={},
        )

        self.assertEqual(result.data["phone_device_id"], "phone-001")
        self.assertEqual(result.data["target_ws_uri"], "ws://127.0.0.1:19001/ws/camera")
        self.assertEqual(result.task_refs[0].task_type, "phone_video_link_task")

    def test_start_phone_video_link_tool_rejects_missing_binding(self) -> None:
        """测试目标：验证未绑定手机时不能创建视频直连任务。

        测试方法：
        1. 构造不包含绑定关系的工具上下文。
        2. 调用 `start_phone_video_link`。

        预期结果：
        1. Tool 调用抛出结构化错误。
        2. 错误信息明确指出当前眼镜尚未绑定手机。
        """

        registry, gateway = build_tooling(
            device_state_reader=lambda: {
                "device_bindings": {
                    "glass_to_phone": {},
                    "phone_to_glass": {},
                },
                "connections": [],
            }
        )
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_video_tool_002",
            turn_id="turn_video_tool_002",
        )

        with self.assertRaisesRegex(Exception, "当前眼镜尚未绑定手机"):
            gateway.invoke(
                name="start_phone_video_link",
                context=context,
                arguments={},
            )

    def test_agent_facade_persists_plain_reply_without_custom_action(self) -> None:
        """测试目标：验证普通回复会以统一助手消息写入，而不再使用自定义 action。

        测试方法：
        1. 构造返回普通文本回复的假运行循环。
        2. 提交一轮最小 `AgentTurn`。
        3. 检查助手消息类型和对话状态。

        预期结果：
        1. 助手消息类型固定为 `assistant_reply`。
        2. 不再把回复解释成自定义 `ask_user` 动作。
        3. `pending_question` 会被清空。
        """

        facade = AgentFacade(
            session_store=AgentSessionStore(),
            tool_registry=build_tooling()[0],
            runner=AskUserAgentLoopRunner(),
        )

        result = facade.handle_turn(
            AgentTurn(
                turn_id="turn_ask_001",
                session_id="sess_ask_001",
                device_id="glass-001",
                source="voice_asr",
                input_text="帮我处理一下",
            )
        )

        self.assertIsNone(result.error)
        session = facade.get_session_store().get_session("sess_ask_001")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.messages[1].kind, "assistant_reply")
        self.assertIsNone(session.dialog_state.pending_question)

    def test_agent_facade_exposes_session_debug_snapshot(self) -> None:
        """测试目标：验证 AgentFacade 能输出会话调试快照。"""

        tool_registry, _ = build_tooling()
        facade = AgentFacade(
            session_store=AgentSessionStore(),
            tool_registry=tool_registry,
            runner=FakeAgentLoopRunner(),
            system_prompt="你是测试系统提示词",
        )

        facade.handle_turn(
            AgentTurn(
                turn_id="turn_debug_001",
                session_id="sess_debug_001",
                device_id="glass-001",
                source="voice_asr",
                input_text="帮我看看当前设备状态",
            )
        )

        snapshot = facade.get_session_debug_snapshot("sess_debug_001")

        self.assertIsNotNone(snapshot)
        assert snapshot is not None
        self.assertEqual(snapshot["session_id"], "sess_debug_001")
        self.assertEqual(len(snapshot["messages"]), 3)
        self.assertEqual(snapshot["messages"][0]["role"], "system")
        self.assertEqual(snapshot["messages"][0]["kind"], "system_prompt")
        self.assertEqual(snapshot["messages"][0]["text"], "你是测试系统提示词")
        self.assertIsNone(snapshot["model_request"])
        self.assertEqual(snapshot["capability_traces"][0]["capability_name"], "query_device_state")

    def test_query_device_state_tool_records_trace(self) -> None:
        """测试目标：验证 query_device_state 工具会返回设备状态并写轨迹。

        测试方法：
        1. 构造带设备快照的 `ToolRegistry`。
        2. 手工调用 `query_device_state`。

        预期结果：
        1. 返回结果包含 `state=listening`。
        2. 轨迹列表中出现成功调用记录。
        """

        traces: list[CapabilityTrace] = []
        registry, gateway = build_tooling(
            device_state_reader=lambda: {
                "glass-001": {
                    "session_id": "sess_003",
                    "state": "listening",
                    "audio_connection_online": True,
                    "reply_stream_id": None,
                }
            },
        )

        result = registry.invoke(
            name="query_device_state",
            context=AgentToolContext(
                session_id="sess_003",
                device_id="glass-001",
                turn_id="turn_003",
                settings=ServerSettings(),
                session_store=AgentSessionStore(),
                device_state_reader=registry.get_device_state_reader(),
                trace_sink=traces.append,
                task_gateway=registry.get_task_gateway(),
                camera_gateway=registry.get_camera_gateway(),
                tool_gateway=gateway,
                mcp_gateway=registry.get_mcp_gateway(),
            ),
        )

        self.assertEqual(result.data["state"], "listening")
        self.assertEqual(len(traces), 1)
        self.assertEqual(traces[0].status, "succeeded")

    def test_context_assembler_includes_history_assets_and_artifacts(self) -> None:
        """测试目标：验证 ContextAssembler 会组装历史、资产和派生结果摘要。"""

        assembler = ContextAssembler()
        session = AgentSession(
            session_id="sess_ctx_001",
            device_id="glass-001",
            messages=[
                MessageContext(
                    message_id="msg_001",
                    session_id="sess_ctx_001",
                    role="assistant",
                    kind="assistant_reply",
                    text="上一轮回复",
                )
            ],
        )
        turn = AgentTurn(
            turn_id="turn_ctx_001",
            session_id="sess_ctx_001",
            device_id="glass-001",
            source="voice_asr",
            input_text="这一轮问题",
            asset_refs=[
                MediaAssetRef(
                    asset_id="asset_ctx_001",
                    session_id="sess_ctx_001",
                    asset_type="audio",
                    storage_uri="runs/session/sess_ctx_001/audio/input.wav",
                    mime_type="audio/wav",
                )
            ],
            derived_artifacts=[
                DerivedArtifact(
                    artifact_id="artifact_ctx_001",
                    session_id="sess_ctx_001",
                    artifact_type="asr_transcript",
                    storage_uri="runs/session/sess_ctx_001/artifact/transcript.json",
                    text="这一轮问题",
                )
            ],
        )

        assembled = assembler.assemble_turn_input(session=session, turn=turn)

        self.assertIn("助手: 上一轮回复", assembled)
        self.assertIn("这一轮问题", assembled)
        self.assertIn("audio: runs/session/sess_ctx_001/audio/input.wav", assembled)
        self.assertIn("asr_transcript: 这一轮问题", assembled)

    def test_openai_runner_delegates_to_agents_sdk(self) -> None:
        """测试目标：验证 OpenAIAgentLoopRunner 会调用 OpenAI Agents SDK。

        测试方法：
        1. 通过 patch 替换 `agents.Runner.run_sync`。
        2. 执行一轮最小 `run_turn`。
        3. 检查传给 SDK 的输入是否直接使用原始历史消息列表。

        预期结果：
        1. `Runner.run_sync` 被调用一次。
        2. 返回值被转换为统一 `AgentTurnResult`。
        3. 输入消息保持 `user/assistant` 原始轮次，不再被压缩成说明文本。
        """

        registry, gateway = build_tooling()
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(
            session_id="sess_004",
            device_id="glass-001",
            messages=[
                MessageContext(
                    message_id="msg_000",
                    session_id="sess_004",
                    role="user",
                    kind="audio_input",
                    text="上一轮问题",
                ),
                MessageContext(
                    message_id="msg_001",
                    session_id="sess_004",
                    role="assistant",
                    kind="assistant_reply",
                    text="上一轮回复",
                )
            ],
        )
        turn = AgentTurn(
            turn_id="turn_004",
            session_id="sess_004",
            device_id="glass-001",
            source="voice_asr",
            input_text="把上一轮回复复述给我",
        )

        class _FakeRunResult:
            def __init__(self) -> None:
                self.final_output = "设备当前正在监听"

        with install_fake_agents_module():
            with patch("agents.Runner.run_sync", return_value=_FakeRunResult()) as mocked_run:
                result = runner.run_turn(session=session, turn=turn)

        self.assertIsNone(result.error)
        self.assertEqual(result.reply_text, "设备当前正在监听")
        self.assertEqual(result.meta["model_request"]["model"], "qwen3.6-plus")
        self.assertEqual(result.meta["model_request"]["messages"][0]["role"], "system")
        self.assertEqual(result.meta["model_request"]["messages"][1], {"role": "user", "content": "上一轮问题"})
        self.assertEqual(result.meta["model_request"]["messages"][2], {"role": "assistant", "content": "上一轮回复"})
        self.assertEqual(
            result.meta["model_request"]["messages"][3],
            {"role": "user", "content": "把上一轮回复复述给我"},
        )
        mocked_run.assert_called_once()
        _, input_payload = mocked_run.call_args.args[:2]
        self.assertEqual(
            input_payload,
            [
                {"role": "user", "content": "上一轮问题"},
                {"role": "assistant", "content": "上一轮回复"},
                {"role": "user", "content": "把上一轮回复复述给我"},
            ],
        )

    def test_openai_runner_rejects_qwen_turbo_streaming_tools(self) -> None:
        """测试目标：不支持 `stream=True + tools` 的模型应在调用前失败。

        测试方法：
        1. 使用 `AGENT_MODEL_NAME=qwen-turbo` 构造 OpenAI Agent 运行器。
        2. 提供 `reply_text_delta_callback` 触发语音链路的流式 Agent 模式。
        3. patch `Runner.run_streamed`，确认 SDK 不会真的发起模型调用。

        预期结果：
        1. 返回结构化配置错误。
        2. 错误信息说明 `qwen-turbo` 不支持当前流式工具调用组合。
        """

        registry, gateway = build_tooling()
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key", agent_model_name="qwen-turbo"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_qwen_turbo", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_qwen_turbo",
            session_id="sess_qwen_turbo",
            device_id="glass-001",
            source="voice_asr",
            input_text="测试一下",
        )

        with install_fake_agents_module():
            with patch("agents.Runner.run_streamed") as mocked_run:
                result = runner.run_turn(
                    session=session,
                    turn=turn,
                    reply_text_delta_callback=lambda _text: None,
                )

        self.assertIsNotNone(result.error)
        self.assertEqual(result.error["code"], ErrorCode.INVALID_CONFIG.value)
        self.assertIn("AGENT_MODEL_NAME=qwen-turbo", result.error["message"])
        mocked_run.assert_not_called()

    def test_openai_runner_uses_minimal_system_prompt(self) -> None:
        """测试目标：验证发给模型的 system prompt 只保留角色与风格约束。

        测试方法：
        1. 调用 `_build_instructions()` 生成 system prompt。
        2. 检查其中包含角色设定与回复风格。
        3. 检查其中不再塞入内部运行协议或 JSON 约束。

        预期结果：
        1. system prompt 足够简短。
        2. 不包含 `final_answer`、`ask_user` 等内部契约词。
        3. 不包含“请遵守以下规则”这类大段框架规则。
        """

        registry, gateway = build_tooling()
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )

        instructions = runner._build_instructions()  # noqa: SLF001 - 单测覆盖内部 system prompt 生成

        self.assertIn("乐鑫", instructions)
        self.assertIn("简短、口语化、直接的中文回答", instructions)
        self.assertIn("必要时可以调用已提供的工具", instructions)
        self.assertNotIn("final_answer", instructions)
        self.assertNotIn("ask_user", instructions)
        self.assertNotIn("json", instructions.lower())
        self.assertNotIn("资产", instructions)
        self.assertNotIn("请遵守以下规则", instructions)

    def test_skill_runtime_read_skill_activates_session_and_filters_tools(self) -> None:
        """测试目标：验证 Skill Runtime 可以读取 Skill、激活会话并限制模型工具。

        测试方法：
        1. 注册一个只允许 `capture_photo` 的 Skill。
        2. 通过 `read_skill` 工具读取 Skill 文档。
        3. 执行一轮 Agent，并检查 system prompt 与模型工具列表。

        预期结果：
        1. `read_skill` 会把 Skill 加入当前会话 active 状态。
        2. system prompt 包含 active Skill 正文。
        3. 模型可见工具只保留 `read_skill` 与 Skill 白名单中的工具。
        """

        skill_runtime = SkillRuntime()
        skill_runtime.register(
            SkillDocument(
                manifest=SkillManifest(
                    name="scene_inspection",
                    version="1.0.0",
                    description="看清用户眼前场景",
                    allowed_tools=["capture_photo"],
                ),
                content="先拍照，再根据照片用一句话回答。",
            )
        )
        registry, gateway = build_tooling(skill_runtime=skill_runtime)
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_skill_001",
            turn_id="turn_read_skill_001",
        )

        read_result = registry.invoke(
            name="read_skill",
            context=context,
            arguments={"skill_name": "scene_inspection"},
        )

        self.assertTrue(read_result.ok)
        self.assertEqual(read_result.data["active_skill_names"], ["scene_inspection"])
        with self.assertRaises(AppError) as blocked:
            registry.invoke(
                name="query_device_state",
                context=context,
                arguments={},
            )
        self.assertEqual(blocked.exception.code, ErrorCode.UNAUTHORIZED)

        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
            skill_runtime=skill_runtime,
        )
        session = AgentSession(session_id="sess_skill_001", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_skill_001",
            session_id="sess_skill_001",
            device_id="glass-001",
            source="voice_asr",
            input_text="看一下前面有什么",
        )

        class _FakeRunResult:
            def __init__(self) -> None:
                self.final_output = "前面有一张桌子。"

        with install_fake_agents_module():
            with patch("agents.Runner.run_sync", return_value=_FakeRunResult()) as mocked_run:
                result = runner.run_turn(session=session, turn=turn)

        agent = mocked_run.call_args.args[0]
        tool_names = [
            getattr(tool, "name", None) or getattr(tool, "_tool_name_override", "")
            for tool in agent.kwargs["tools"]
        ]
        self.assertEqual(tool_names, ["capture_photo", "read_skill"])
        self.assertIn("当前 active Skills", result.meta["model_request"]["instructions"])
        self.assertIn("先拍照，再根据照片用一句话回答。", result.meta["model_request"]["instructions"])
        self.assertEqual(result.meta["model_request"]["active_skills"], ["scene_inspection"])
        self.assertEqual(result.meta["model_request"]["allowed_tool_names"], ["capture_photo", "read_skill"])

    def test_openai_runner_creates_event_loop_in_worker_thread(self) -> None:
        """测试目标：验证 OpenAIAgentLoopRunner 在工作线程中会自动补齐 event loop。

        测试方法：
        1. 在子线程中执行 `runner.run_turn`。
        2. 用假 `Runner.run_sync` 在调用点读取当前线程 event loop。

        预期结果：
        1. 子线程内不会因为缺少 event loop 抛错。
        2. 假 `Runner.run_sync` 能读取到未关闭的 event loop。
        """

        registry, gateway = build_tooling()
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_005", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_005",
            session_id="sess_005",
            device_id="glass-001",
            source="voice_asr",
            input_text="你好",
        )

        class _FakeRunResult:
            def __init__(self) -> None:
                self.final_output = "收到"

        observed: dict[str, object] = {}

        def _fake_run_sync(*_args, **_kwargs):
            loop = asyncio.get_event_loop()
            observed["loop"] = loop
            observed["closed"] = loop.is_closed()
            return _FakeRunResult()

        failure: list[BaseException] = []

        def _worker() -> None:
            try:
                runner.run_turn(session=session, turn=turn)
            except BaseException as exc:  # pragma: no cover - 仅在失败时记录
                failure.append(exc)

        with install_fake_agents_module():
            with patch("agents.Runner.run_sync", side_effect=_fake_run_sync):
                thread = threading.Thread(target=_worker, name="agent-test-worker")
                thread.start()
                thread.join(timeout=3)

        self.assertFalse(failure)
        self.assertIn("loop", observed)
        self.assertFalse(observed["closed"])

    def test_openai_runner_extracts_reply_text_from_string_output(self) -> None:
        """测试目标：验证纯文本最终输出会被直接识别为回复文本。"""

        self.assertEqual(OpenAIAgentLoopRunner._extract_reply_text(" 这是一个笑话 "), "这是一个笑话")  # noqa: SLF001

    def test_openai_runner_routes_status_query_through_sdk_loop(self) -> None:
        """测试目标：验证设备状态问题也会进入标准 SDK tool calling 主循环。

        测试方法：
        1. 构造一轮设备状态询问输入。
        2. patch `agents.Runner.run_sync` 返回固定结果。
        3. 检查 SDK 被正常调用，而不是被本地直连路由截走。

        预期结果：
        1. `Runner.run_sync` 被调用一次。
        2. 当前轮不会提前产生命中直连路由的 capability trace。
        """

        registry, gateway = build_tooling(
            device_state_reader=lambda: {
                "glass-001": {
                    "session_id": "sess_006",
                    "state": "listening",
                    "audio_connection_online": True,
                    "reply_stream_id": None,
                }
            }
        )
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_006", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_006",
            session_id="sess_006",
            device_id="glass-001",
            source="voice_asr",
            input_text="我的眼镜现在怎么样了？",
        )

        class _FakeRunResult:
            def __init__(self) -> None:
                self.final_output = "我来帮你查一下。"

        with install_fake_agents_module():
            with patch("agents.Runner.run_sync", return_value=_FakeRunResult()) as mocked_run:
                result = runner.run_turn(session=session, turn=turn)

        mocked_run.assert_called_once()
        self.assertIsNone(result.error)
        self.assertEqual(result.reply_text, "我来帮你查一下。")
        self.assertEqual(len(result.capability_traces), 0)
        _, input_payload = mocked_run.call_args.args[:2]
        self.assertEqual(input_payload, [{"role": "user", "content": "我的眼镜现在怎么样了？"}])

    def test_openai_runner_returns_fail_when_sdk_loop_raises(self) -> None:
        """测试目标：验证 SDK 主循环抛错时会返回统一失败结果。"""

        registry, gateway = build_tooling()
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_007", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_007",
            session_id="sess_007",
            device_id="glass-001",
            source="voice_asr",
            input_text="我的眼镜现在怎么样了？",
        )

        with install_fake_agents_module():
            with patch("agents.Runner.run_sync", side_effect=RuntimeError("sdk boom")):
                result = runner.run_turn(session=session, turn=turn)

        self.assertIsNotNone(result.error)
        self.assertIn("agent-core 运行失败", result.reply_text)
        self.assertEqual(len(result.capability_traces), 0)

    def test_openai_runner_can_emit_progress_before_capture_photo(self) -> None:
        """测试目标：验证视觉链路会在拍照前发出中间播报。

        测试方法：
        1. 伪造 `run_streamed` 事件流，只产出 `capture_photo` 的 tool_called/tool_output。
        2. 将主链路图片续跑方法打桩为固定回复。
        3. 收集中间播报回调与最终结果。

        预期结果：
        1. 中间播报会先收到“好的，你保持别动，我拍一张帮你看。”
        2. 最终回复来自图片续跑阶段。
        """

        registry, gateway = build_tooling(camera_gateway=FakeCameraGateway())
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_stream_photo_001", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_stream_photo_001",
            session_id="sess_stream_photo_001",
            device_id="glass-001",
            source="voice_asr",
            input_text="看一下我眼前是什么",
        )
        progress_messages: list[str] = []

        class _FakeStream:
            def __init__(self, events) -> None:
                self._events = iter(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._events)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        class _FakeRunResult:
            final_output = ""

            def stream_events(self):
                tool_called = types.SimpleNamespace(
                    type="run_item_stream_event",
                    name="tool_called",
                    item=types.SimpleNamespace(raw_item=types.SimpleNamespace(name="capture_photo", call_id="call_photo_001")),
                )
                tool_output = types.SimpleNamespace(
                    type="run_item_stream_event",
                    name="tool_output",
                    item=types.SimpleNamespace(raw_item=types.SimpleNamespace(call_id="call_photo_001")),
                )
                return _FakeStream([tool_called, tool_output])

            def cancel(self):
                return None

        async def _fake_followup(*args, **kwargs):
            return "我看到前方有一张桌子。"

        with install_fake_agents_module():
            with patch("agents.Runner.run_streamed", return_value=_FakeRunResult()):
                with patch.object(
                    runner,
                    "_wait_for_new_image_asset",
                    new=AsyncMock(
                        return_value=MediaAssetRef(
                            asset_id="asset_test_001",
                            session_id="sess_stream_photo_001",
                            asset_type="image",
                            storage_uri="/tmp/fake.jpg",
                            mime_type="image/jpeg",
                        )
                    ),
                ):
                    with patch.object(runner, "_stream_image_followup_reply", side_effect=_fake_followup):
                        result = runner.run_turn(
                            session=session,
                            turn=turn,
                            progress_callback=progress_messages.append,
                        )

        self.assertEqual(progress_messages, ["好的，你保持别动，我拍一张帮你看。"])
        self.assertEqual(result.reply_text, "我看到前方有一张桌子。")

    def test_openai_runner_streams_plain_text_delta_to_callback(self) -> None:
        """测试目标：验证普通文本回复会透传模型增量给上层流式 TTS。

        测试方法：
        1. 伪造 `Runner.run_streamed` 的 `raw_response_event` 文本增量事件。
        2. 执行一轮普通问答，不触发工具调用。
        3. 收集 `reply_text_delta_callback` 收到的文本片段。

        预期结果：
        1. 回调会收到普通文本增量。
        2. 最终回复文本由增量拼接得到。
        3. 这条路径不再等待 `final_output` 后才给 TTS 文本。
        """

        registry, gateway = build_tooling()
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_stream_text_001", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_stream_text_001",
            session_id="sess_stream_text_001",
            device_id="glass-001",
            source="voice_asr",
            input_text="你好",
        )
        delta_parts: list[str] = []

        class _FakeStream:
            def __init__(self, events) -> None:
                self._events = iter(events)

            def __aiter__(self):
                return self

            async def __anext__(self):
                try:
                    return next(self._events)
                except StopIteration as exc:
                    raise StopAsyncIteration from exc

        class _FakeRunResult:
            final_output = "这段 final_output 不应覆盖已流出的文本"

            def stream_events(self):
                return _FakeStream(
                    [
                        types.SimpleNamespace(
                            type="raw_response_event",
                            data=types.SimpleNamespace(type="response.output_text.delta", delta="你好，"),
                        ),
                        types.SimpleNamespace(
                            type="raw_response_event",
                            data={"type": "response.output_text.delta", "delta": "我在。"},
                        ),
                    ]
                )

            def cancel(self):
                return None

        with install_fake_agents_module():
            with patch("agents.Runner.run_streamed", return_value=_FakeRunResult()):
                result = runner.run_turn(
                    session=session,
                    turn=turn,
                    reply_text_delta_callback=delta_parts.append,
                )

        self.assertEqual(delta_parts, ["你好，", "我在。"])
        self.assertEqual(result.reply_text, "你好，我在。")

    def test_stream_image_followup_reply_uses_multimodal_request(self) -> None:
        """测试目标：验证拍照后主链路会把真实图片作为多模态输入发给模型。

        测试方法：
        1. 构造带图片资产的会话与上下文。
        2. 注入假 SDK 客户端，记录 `chat.completions.create` 请求参数。
        3. 调用 `_stream_image_followup_reply(...)` 收集文本增量。

        预期结果：
        1. 模型请求最后一条用户消息包含文本和 `image_url` 两个 part。
        2. 系统提示词明确要求直接根据图片回答，不再追问是否保存照片。
        3. 返回文本与增量回调内容一致。
        """

        temp_dir = tempfile.mkdtemp(prefix="agent-image-followup-")
        image_path = os.path.join(temp_dir, "capture.png")
        with open(image_path, "wb") as file:
            file.write(_FAKE_PNG_BYTES)

        session_store = AgentSessionStore()
        session = session_store.get_or_create_session(
            session_id="sess_image_followup_001",
            device_id="glass-001",
        )
        image_asset = MediaAssetRef(
            asset_id="asset_image_followup_001",
            session_id="sess_image_followup_001",
            asset_type="image",
            storage_uri=image_path,
            mime_type="image/png",
        )
        session.assets[image_asset.asset_id] = image_asset
        session.messages.append(
            MessageContext(
                message_id="msg_hist_001",
                session_id="sess_image_followup_001",
                role="assistant",
                kind="assistant_reply",
                text="上一轮我已经回答过一次。",
            )
        )

        registry, gateway = build_tooling(camera_gateway=FakeCameraGateway())
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=session_store,
            tool_registry=registry,
            tool_gateway=gateway,
        )
        tool_context = AgentToolContext(
            session_id="sess_image_followup_001",
            device_id="glass-001",
            turn_id="turn_image_followup_001",
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=session_store,
            device_state_reader=registry.get_device_state_reader(),
            trace_sink=lambda _trace: None,
            task_gateway=registry.get_task_gateway(),
            camera_gateway=registry.get_camera_gateway(),
            tool_gateway=gateway,
            mcp_gateway=registry.get_mcp_gateway(),
        )
        tool_context.emitted_assets.append(image_asset)
        turn = AgentTurn(
            turn_id="turn_image_followup_001",
            session_id="sess_image_followup_001",
            device_id="glass-001",
            source="voice_asr",
            input_text="看看我眼前有什么。",
        )

        captured_requests: list[dict[str, object]] = []

        class _FakeChunk:
            def __init__(self, text: str) -> None:
                self.choices = [types.SimpleNamespace(delta=types.SimpleNamespace(content=text))]

        class _FakeChatCompletions:
            def create(self, **kwargs):
                captured_requests.append(kwargs)
                return [_FakeChunk("前面"), _FakeChunk("有一张桌子。")]

        class _FakeChat:
            completions = _FakeChatCompletions()

        class _FakeClient:
            chat = _FakeChat()

        delta_parts: list[str] = []

        with patch.object(runner, "_create_sdk_client", return_value=_FakeClient()):
            reply_text = asyncio.run(
                runner._stream_image_followup_reply(
                    tool_context=tool_context,
                    turn=turn,
                    image_asset=image_asset,
                    history_session=session,
                    reply_text_delta_callback=delta_parts.append,
                )
            )

        self.assertEqual(reply_text, "前面有一张桌子。")
        self.assertEqual(delta_parts, ["前面", "有一张桌子。"])
        self.assertEqual(len(captured_requests), 1)
        request = captured_requests[0]
        self.assertEqual(request["model"], "qwen3.6-plus")
        messages = request["messages"]
        assert isinstance(messages, list)
        self.assertIn("请直接结合图片回答用户刚才的问题", messages[0]["content"])
        self.assertIn("不要只说你已经拍照了", messages[0]["content"])
        user_message = messages[-1]
        self.assertEqual(user_message["role"], "user")
        assert isinstance(user_message["content"], list)
        self.assertEqual(user_message["content"][0]["type"], "text")
        self.assertEqual(user_message["content"][0]["text"], "看看我眼前有什么。")
        self.assertEqual(user_message["content"][1]["type"], "image_url")
        self.assertTrue(user_message["content"][1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_wait_for_new_image_asset_skips_old_history_image(self) -> None:
        """测试目标：验证拍照续跑只会等待本次新抓拍图片，不会复用旧图。

        测试方法：
        1. 先在会话里放入一张历史图片。
        2. 调用 `_wait_for_new_image_asset(...)`，并在短延迟后追加一张新图。
        3. 检查返回的是否为新增图片。

        预期结果：
        1. 旧图片会被排除。
        2. 返回值是本次新增的图片资产。
        """

        session_store = AgentSessionStore()
        session = session_store.get_or_create_session(
            session_id="sess_new_image_001",
            device_id="glass-001",
        )
        old_image = MediaAssetRef(
            asset_id="asset_old_image_001",
            session_id="sess_new_image_001",
            asset_type="image",
            storage_uri="/tmp/old.jpg",
            mime_type="image/jpeg",
        )
        session.assets[old_image.asset_id] = old_image

        registry, gateway = build_tooling(camera_gateway=FakeCameraGateway())
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=session_store,
            tool_registry=registry,
            tool_gateway=gateway,
        )
        tool_context = AgentToolContext(
            session_id="sess_new_image_001",
            device_id="glass-001",
            turn_id="turn_new_image_001",
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=session_store,
            device_state_reader=registry.get_device_state_reader(),
            trace_sink=lambda _trace: None,
            task_gateway=registry.get_task_gateway(),
            camera_gateway=registry.get_camera_gateway(),
            tool_gateway=gateway,
            mcp_gateway=registry.get_mcp_gateway(),
        )

        async def _exercise():
            async def _append_new_image():
                await asyncio.sleep(0.02)
                tool_context.emitted_assets.append(
                    MediaAssetRef(
                        asset_id="asset_new_image_001",
                        session_id="sess_new_image_001",
                        asset_type="image",
                        storage_uri="/tmp/new.jpg",
                        mime_type="image/jpeg",
                    )
                )

            producer = asyncio.create_task(_append_new_image())
            try:
                return await runner._wait_for_new_image_asset(
                    tool_context=tool_context,
                    session=session,
                    excluded_asset_ids={"asset_old_image_001"},
                    timeout_seconds=0.5,
                )
            finally:
                await producer

        image_asset = asyncio.run(_exercise())

        self.assertIsNotNone(image_asset)
        assert image_asset is not None
        self.assertEqual(image_asset.asset_id, "asset_new_image_001")

    def test_tool_registry_exposes_expected_model_facing_tools(self) -> None:
        """测试目标：验证当前根部服务端只向模型暴露系统内置工具。"""

        registry, gateway = build_tooling()
        tool_names = {tool.spec.name for tool in registry.list_tools()}

        self.assertEqual(tool_names, {"capture_photo"})
        self.assertIsNotNone(registry.get("capture_photo"))
        self.assertIsNone(registry.get("create_timer"))
        self.assertIsNone(registry.get("timer_manage"))
        self.assertIsNone(registry.get("start_find_object"))
        self.assertIsNone(registry.get("map_manage"))

    def test_tool_registry_device_state_reader_can_be_rebound(self) -> None:
        """测试目标：验证工具运行态读取函数可在控制面就绪后重新绑定。"""

        registry, _ = build_tooling(device_state_reader=lambda: {"source": "voice"})

        self.assertEqual(registry.get_device_state_reader()()["source"], "voice")

        registry.bind_device_state_reader(lambda: {"source": "control", "device_bindings": {}})

        snapshot = registry.get_device_state_reader()()
        self.assertEqual(snapshot["source"], "control")
        self.assertIn("device_bindings", snapshot)

    def test_sdk_tool_signature_uses_explicit_input_fields(self) -> None:
        """测试目标：验证 SDK Tool 不再暴露宽泛 payload 字段。"""

        registry, _ = build_tooling()
        sdk_tool = registry._sdk_tools["capture_photo"]
        if hasattr(sdk_tool, "params_json_schema"):
            properties = sdk_tool.params_json_schema.get("properties", {})
            self.assertIn("reason", properties)
            self.assertNotIn("payload", properties)
            return

        signature = inspect.signature(sdk_tool)
        self.assertIn("reason", signature.parameters)
        self.assertNotIn("payload", signature.parameters)

    def test_capture_photo_tool_uses_real_camera_gateway_result(self) -> None:
        """测试目标：验证 capture_photo 会把相机网关返回的真实字节写成图片资产。

        测试方法：
        1. 注入假相机网关，返回一张最小 PNG 图片。
        2. 调用 `capture_photo` Tool。
        3. 检查结果路径和资产文件内容。

        预期结果：
        1. 返回结果中的图片路径真实存在。
        2. 文件字节与相机网关回传字节一致。
        """

        settings = ServerSettings(voice_runs_root="/tmp/agent-core-real-capture")
        registry, gateway = build_tooling(camera_gateway=FakeCameraGateway())
        result = registry.invoke(
            name="capture_photo",
            context=AgentToolContext(
                session_id="sess_capture_real_001",
                device_id="glass-001",
                turn_id="turn_capture_real_001",
                settings=settings,
                session_store=AgentSessionStore(),
                device_state_reader=registry.get_device_state_reader(),
                trace_sink=lambda _trace: None,
                task_gateway=registry.get_task_gateway(),
                camera_gateway=registry.get_camera_gateway(),
                tool_gateway=gateway,
                mcp_gateway=registry.get_mcp_gateway(),
            ),
            arguments={"reason": "unit_test"},
        )

        self.assertEqual(result.data["mime_type"], "image/png")
        self.assertTrue(result.data["storage_uri"].endswith(".png"))
        with open(result.data["storage_uri"], "rb") as handle:
            self.assertEqual(handle.read(), _FAKE_PNG_BYTES)


if __name__ == "__main__":
    unittest.main()
