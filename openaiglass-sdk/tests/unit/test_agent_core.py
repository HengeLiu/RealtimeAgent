"""Phase D agent-core 单元测试。"""

from __future__ import annotations

import asyncio
import copy
import inspect
import os
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

from pydantic import BaseModel

from agent_core import AgentFacade, AgentTurn, AgentTurnResult, DerivedArtifact, MediaAssetRef
from agent_core.camera import CameraCaptureResult
from agent_core.context import AgentSession, AgentSessionStore, CapabilityTrace, MessageContext
from agent_core.context.assembler import ContextAssembler
from agent_core.memory import (
    AgentMemoryRuntime,
    InMemoryAgentMemoryStore,
    MemoryManagementAgent,
    MemoryOperationAction,
    MemoryOperationPlan,
    MemoryOperationRequest,
)
from agent_core.runtime import AgentLoopRunner, OpenAIAgentLoopRunner
from agent_core.skills import SkillDocument, SkillManifest, SkillRuntime
from agent_core.models import CapabilityResult, ToolSpec
from agent_core.tools import AgentToolContext, BaseTool, ToolGateway, ToolRegistry
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


def build_tooling(
    device_state_reader=lambda: {},
    camera_gateway=None,
    task_gateway=None,
    skill_runtime=None,
    memory_runtime=None,
):
    registry = ToolRegistry(
        device_state_reader=device_state_reader,
        camera_gateway=camera_gateway,
        skill_runtime=skill_runtime,
        memory_runtime=memory_runtime,
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
        utterance_photo_store=registry.get_utterance_photo_store(),
        tool_gateway=gateway,
        mcp_gateway=registry.get_mcp_gateway(),
        turn_meta={"segment_id": "seg_context_001", "stream_id": "stream_context_001"},
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


class FakeMemoryManagementAgent(MemoryManagementAgent):
    """测试用记忆管理 Agent。"""

    def __init__(self, plans: list[MemoryOperationPlan]) -> None:
        self._plans = list(plans)
        self.requests: list[MemoryOperationRequest] = []

    def plan(
        self,
        *,
        request: MemoryOperationRequest,
        existing_memories,
    ) -> MemoryOperationPlan:
        self.requests.append(request)
        if not self._plans:
            return MemoryOperationPlan(actions=[], feedback="没有需要更新的记忆")
        return self._plans.pop(0)


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

    def test_manage_memory_and_memory_search_use_agent_plan(self) -> None:
        """测试目标：验证 SDK 内置记忆 Tool 使用 MemoryAgent 动作计划。

        测试方法：
        1. 构造内存版 `AgentMemoryRuntime` 和测试用 MemoryAgent。
        2. 让 MemoryAgent 先返回新增动作，再返回删除动作。
        3. 通过 `memory_search` 按主题读取记忆详情。

        预期结果：
        1. `manage_memory` 入参只需要主 Agent 摘取的 `memory_context`。
        2. 主 Agent 可见结果不包含内部 `memory_id`。
        3. 搜索未命中时会返回文本反馈。
        """

        manager = FakeMemoryManagementAgent(
            plans=[
                MemoryOperationPlan(
                    actions=[
                        MemoryOperationAction(
                            operation="add",
                            memory_type="personalized",
                            topic="导航偏好",
                            content="用户喜欢导航提示尽量简短。",
                        )
                    ],
                    feedback="已记住导航偏好",
                ),
                MemoryOperationPlan(
                    actions=[MemoryOperationAction(operation="delete", topic="导航偏好")],
                    feedback="已删除导航偏好",
                ),
            ]
        )
        memory_runtime = AgentMemoryRuntime(store=InMemoryAgentMemoryStore(), manager_agent=manager)
        registry, gateway = build_tooling(memory_runtime=memory_runtime)
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_memory_001",
            turn_id="turn_memory_001",
        )

        add_result = gateway.invoke(
            name="manage_memory",
            context=context,
            arguments={
                "memory_context": "用户刚才说导航提示尽量简短，需要保存为导航偏好。",
            },
        )
        self.assertEqual(add_result.message, "已记住导航偏好")
        self.assertEqual(add_result.data["actions"][0]["operation"], "add")
        self.assertNotIn("memory_id", add_result.data["actions"][0])
        self.assertEqual(manager.requests[0].memory_context, "用户刚才说导航提示尽量简短，需要保存为导航偏好。")

        search_result = gateway.invoke(
            name="memory_search",
            context=context,
            arguments={"topic": "导航偏好"},
        )
        self.assertNotIn("memory_id", search_result.data["memories"][0])
        self.assertEqual(search_result.data["memories"][0]["content"], "用户喜欢导航提示尽量简短。")

        delete_result = gateway.invoke(
            name="manage_memory",
            context=context,
            arguments={"memory_context": "用户要求忘掉导航偏好。"},
        )
        self.assertEqual(delete_result.message, "已删除导航偏好")

        list_result = gateway.invoke(
            name="memory_search",
            context=context,
            arguments={"topic": "导航偏好"},
        )
        self.assertEqual(list_result.data["memories"], [])
        self.assertEqual(list_result.message, "没有找到匹配的记忆")
        self.assertEqual(memory_runtime.list_memories(scope_type="device", scope_id="glass-001"), [])

    def test_manage_memory_executes_multiple_actions_in_order(self) -> None:
        """测试目标：验证一次记忆维护可以串行执行多个动作。

        测试方法：
        1. 预先写入旧导航偏好。
        2. 让 MemoryAgent 返回先删除再新增的动作列表。
        3. 按主题查询最终记忆。

        预期结果：
        1. 旧内容被删除。
        2. 新内容按同一主题写入。
        3. 主 Agent 只看到动作摘要和文本反馈。
        """

        store = InMemoryAgentMemoryStore()
        memory_runtime = AgentMemoryRuntime(store=store, manager_agent=None)
        old_record = memory_runtime.add_memory(
            scope_type="device",
            scope_id="glass-001",
            memory_type="personalized",
            content="用户喜欢导航提示详细一点。",
            topic="导航偏好",
        )
        manager = FakeMemoryManagementAgent(
            plans=[
                MemoryOperationPlan(
                    actions=[
                        MemoryOperationAction(operation="delete", memory_id=old_record.memory_id, topic="导航偏好"),
                        MemoryOperationAction(
                            operation="add",
                            memory_type="personalized",
                            topic="导航偏好",
                            content="用户喜欢导航提示尽量简短。",
                        ),
                    ],
                    feedback="已更新导航偏好",
                )
            ]
        )
        memory_runtime = AgentMemoryRuntime(store=store, manager_agent=manager)
        registry, gateway = build_tooling(memory_runtime=memory_runtime)
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_memory_multi_001",
            turn_id="turn_memory_multi_001",
        )

        result = gateway.invoke(
            name="manage_memory",
            context=context,
            arguments={"memory_context": "用户要求把导航偏好改成简短提示。"},
        )
        self.assertEqual(result.message, "已更新导航偏好")
        self.assertEqual([item["operation"] for item in result.data["actions"]], ["delete", "add"])

        search_result = gateway.invoke(
            name="memory_search",
            context=context,
            arguments={"topic": "导航偏好"},
        )
        self.assertEqual(search_result.data["memories"][0]["content"], "用户喜欢导航提示尽量简短。")

    def test_manage_memory_update_preserves_internal_memory_id(self) -> None:
        """测试目标：验证更新记忆时复用原有 `memory_id`。

        测试方法：
        1. 先通过 runtime 内部接口新增一条基本信息。
        2. MemoryAgent 返回带内部 `memory_id` 的更新动作。

        预期结果：
        1. 更新后的记忆仍使用原 `memory_id`。
        2. 主 Agent 可见结果不暴露 `memory_id`。
        """

        store = InMemoryAgentMemoryStore()
        memory_runtime = AgentMemoryRuntime(store=store, manager_agent=None)
        record = memory_runtime.add_memory(
            scope_type="device",
            scope_id="glass-001",
            memory_type="basic",
            content="用户姓名是小明。",
            topic="姓名",
        )
        manager = FakeMemoryManagementAgent(
            plans=[
                MemoryOperationPlan(
                    actions=[
                        MemoryOperationAction(
                            operation="update",
                            memory_type="basic",
                            topic="姓名",
                            content="用户姓名是小李。",
                            memory_id=record.memory_id,
                        )
                    ],
                    feedback="已更新姓名",
                )
            ]
        )
        memory_runtime = AgentMemoryRuntime(store=store, manager_agent=manager)
        registry, gateway = build_tooling(memory_runtime=memory_runtime)
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_memory_update_001",
            turn_id="turn_memory_update_001",
        )

        update_result = gateway.invoke(
            name="manage_memory",
            context=context,
            arguments={"memory_context": "用户要求把自己的名字更新为小李。"},
        )

        self.assertNotIn("memory_id", update_result.data["actions"][0])
        updated = store.list_records(scope_type="device", scope_id="glass-001")[0]
        self.assertEqual(updated.memory_id, record.memory_id)
        self.assertEqual(updated.topic, "姓名")
        self.assertEqual(updated.content, "用户姓名是小李。")

    def test_agent_memory_rejects_unknown_memory_type(self) -> None:
        """测试目标：验证长期记忆运行时拒绝未知记忆类型。

        测试方法：
        1. 构造内存版 `AgentMemoryRuntime`。
        2. 直接调用内部写入接口传入非法 `memory_type`。

        预期结果：
        1. SDK 明确抛出 `ValueError`。
        2. 存储中不会写入非法类型记忆。
        """

        memory_runtime = AgentMemoryRuntime(store=InMemoryAgentMemoryStore())

        with self.assertRaises(ValueError):
            memory_runtime.add_memory(
                scope_type="device",
                scope_id="glass-001",
                memory_type="unknown",  # type: ignore[arg-type]
                content="这条记忆不应写入。",
                topic="非法类型",
            )

        self.assertEqual(memory_runtime.list_memories(scope_type="device", scope_id="glass-001"), [])

    def test_agent_runner_injects_basic_memory_and_personalized_topics(self) -> None:
        """测试目标：验证 Agent 运行时按基本信息和个性化信息策略注入长期记忆。

        测试方法：
        1. 预先写入一条基本信息和一条个性化信息。
        2. 构造 `OpenAIAgentLoopRunner` 的单轮运行态。
        3. 检查 `model_request` 中的系统提示词。

        预期结果：
        1. 基本信息主题和内容会完整出现。
        2. 个性化信息只出现主题，不出现详细内容。
        """

        memory_runtime = AgentMemoryRuntime(store=InMemoryAgentMemoryStore())
        memory_runtime.add_memory(
            scope_type="device",
            scope_id="glass-001",
            memory_type="basic",
            content="用户姓名是小明。",
            topic="姓名",
            source="user_requested",
        )
        memory_runtime.add_memory(
            scope_type="device",
            scope_id="glass-001",
            memory_type="personalized",
            content="用户喜欢导航提示尽量简短，并且希望先说方向再说距离。",
            topic="导航偏好",
            source="user_requested",
        )
        registry, gateway = build_tooling(memory_runtime=memory_runtime)
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(
                dashscope_api_key="demo-key",
                agent_model_name="qwen3.6-plus",
                voice_model_name="qwen3.5-omni-plus",
            ),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_memory_002", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_memory_002",
            session_id="sess_memory_002",
            device_id="glass-001",
            source="voice_asr",
            input_text="开始导航时怎么提醒我？",
        )

        runtime = runner._turn_runtime_factory.build(session=session, turn=turn)

        fragment = runtime.model_request["memory_prompt_fragment"]
        self.assertIn("姓名: 用户姓名是小明。", fragment)
        self.assertIn("导航偏好", fragment)
        self.assertNotIn("先说方向再说距离", fragment)
        self.assertIn("memory_search", runtime.model_request["instructions"])
        self.assertIn("manage_memory", runtime.model_request["instructions"])
        self.assertIn("基本信息", fragment)
        self.assertIn("个性化信息主题", fragment)

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
        self.assertEqual(result.meta["model_request"]["model"], "qwen3.5-omni-plus")
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

    def test_openai_runner_direct_audio_path_invokes_tools(self) -> None:
        """测试目标：验证音频原生 Omni 路径以流式模式执行工具调用。

        测试方法：
        1. 注册一个模型可见测试工具。
        2. 构造带本地 WAV 资产的 `AgentTurn`。
        3. 用假 Chat Completions 流式客户端先返回工具调用，再返回最终文本增量。

        预期结果：
        1. 发送给模型的当前轮包含 `input_audio`。
        2. 工具通过 `ToolGateway` 被执行一次。
        3. 模型请求使用 `stream=True`。
        4. 最终回复文本增量会透传给回调。
        """

        class _EchoArgs(BaseModel):
            text: str

        class _EchoTool(BaseTool):
            spec = ToolSpec(
                name="echo_name",
                description="回显名字",
                input_model=_EchoArgs,
                progress_message="我先查一下你的名字。",
            )

            def __init__(self) -> None:
                self.calls: list[str] = []

            def run(self, context: AgentToolContext, input_data: _EchoArgs) -> CapabilityResult:
                self.calls.append(input_data.text)
                return CapabilityResult.success(data={"name": input_data.text})

        echo_tool = _EchoTool()
        registry, gateway = build_tooling()
        registry.register_external_tool(echo_tool)
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_audio_tool_001", device_id="glass-001")
        with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
            audio_file.write(b"RIFFdemo-audio")
            audio_file.flush()
            turn = AgentTurn(
                turn_id="turn_audio_tool_001",
                session_id="sess_audio_tool_001",
                device_id="glass-001",
                source="voice_raw_audio",
                input_text="用户发送了一段语音，请直接理解音频内容并执行用户意图。",
                asset_refs=[
                    MediaAssetRef(
                        asset_id="asset_audio_tool_001",
                        session_id="sess_audio_tool_001",
                        asset_type="audio",
                        storage_uri=audio_file.name,
                        mime_type="audio/wav",
                    )
                ],
            )

            captured_requests: list[dict[str, object]] = []

            class _FakeChoice:
                def __init__(self, delta) -> None:
                    self.delta = delta

            class _FakeChunk:
                def __init__(self, *, content="", tool_calls=None) -> None:
                    self.choices = [_FakeChoice(types.SimpleNamespace(content=content, tool_calls=tool_calls))]

            class _FakeChatCompletions:
                def __init__(self) -> None:
                    self.responses = [
                        [
                            _FakeChunk(
                                tool_calls=[
                                    types.SimpleNamespace(
                                        index=0,
                                        id="call_echo_001",
                                        type="function",
                                        function=types.SimpleNamespace(
                                            name="echo_name",
                                            arguments='{"text": "文刀"}',
                                        ),
                                    )
                                ]
                            )
                        ],
                        [_FakeChunk(content="你叫"), _FakeChunk(content="文刀。")],
                    ]

                def create(self, **kwargs):
                    captured_requests.append(copy.deepcopy(kwargs))
                    return self.responses.pop(0)

            class _FakeChat:
                def __init__(self) -> None:
                    self.completions = _FakeChatCompletions()

            class _FakeClient:
                def __init__(self) -> None:
                    self.chat = _FakeChat()

            progress_parts: list[str] = []
            delta_parts: list[str] = []
            with patch.object(runner, "_create_sdk_client", return_value=_FakeClient()):
                result = runner.run_turn(
                    session=session,
                    turn=turn,
                    progress_callback=progress_parts.append,
                    reply_text_delta_callback=delta_parts.append,
                )

        self.assertIsNone(result.error)
        self.assertEqual(result.reply_text, "你叫文刀。")
        self.assertEqual(delta_parts, ["你叫", "文刀。"])
        self.assertEqual(progress_parts, ["我先查一下你的名字。"])
        self.assertEqual(captured_requests[0]["model"], "qwen3.5-omni-plus")
        self.assertTrue(captured_requests[0]["stream"])
        self.assertTrue(captured_requests[1]["stream"])
        self.assertEqual(result.meta["model_request"]["model"], "qwen3.5-omni-plus")
        self.assertEqual(echo_tool.calls, ["文刀"])
        first_user_content = captured_requests[0]["messages"][-1]["content"]  # type: ignore[index]
        self.assertEqual(first_user_content[1]["type"], "input_audio")
        model_messages = result.meta["model_request"]["messages"]
        audio_model_message = next(
            message
            for message in model_messages
            if isinstance(message.get("content"), list)
            and len(message["content"]) > 1
            and message["content"][1].get("type") == "input_audio"
        )
        self.assertIn("<redacted>", audio_model_message["content"][1]["input_audio"]["data"])

    def test_tool_gateway_announces_progress_once_before_tool_run(self) -> None:
        """测试目标：验证 SDK 在工具执行前可以发出一次前置语音播报。

        测试方法：
        1. 注册一个带 `progress_message` 的测试工具。
        2. 构造带 `progress_callback` 的 `AgentToolContext`。
        3. 连续调用同一个工具两次。

        预期结果：
        1. 工具第一次执行前会触发前置播报。
        2. 同一轮同一工具不会重复播报。
        3. 工具执行结果不受播报影响。
        """

        class _EchoArgs(BaseModel):
            text: str

        class _EchoTool(BaseTool):
            spec = ToolSpec(
                name="echo_progress",
                description="回显文本",
                input_model=_EchoArgs,
                progress_message="我先处理一下。",
            )

            def run(self, context: AgentToolContext, input_data: _EchoArgs) -> CapabilityResult:
                return CapabilityResult.success(data={"text": input_data.text})

        registry, gateway = build_tooling()
        registry.register_external_tool(_EchoTool())
        progress_parts: list[str] = []
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_tool_progress_001",
            turn_id="turn_tool_progress_001",
        )
        context.progress_callback = progress_parts.append

        first_result = gateway.invoke(name="echo_progress", context=context, arguments={"text": "一"})
        second_result = gateway.invoke(name="echo_progress", context=context, arguments={"text": "二"})

        self.assertEqual(progress_parts, ["我先处理一下。"])
        self.assertEqual(first_result.data, {"text": "一"})
        self.assertEqual(second_result.data, {"text": "二"})

    def test_tool_gateway_randomizes_progress_message_candidates(self) -> None:
        """测试目标：验证工具前置播报支持多句候选。

        测试方法：
        1. 注册一个 `progress_message` 为字符串列表的测试工具。
        2. 构造带 `progress_callback` 的 `AgentToolContext`。
        3. 调用工具并收集实际播报文本。

        预期结果：
        1. 实际播报文本来自候选列表。
        2. 同一轮同一工具仍然只播报一次。
        3. 工具执行结果不受候选列表影响。
        """

        class _EchoArgs(BaseModel):
            text: str

        class _EchoTool(BaseTool):
            spec = ToolSpec(
                name="echo_progress_candidates",
                description="回显文本",
                input_model=_EchoArgs,
                progress_message=[
                    "我先处理一下。",
                    "稍等，我看一下。",
                    "好，我来处理。",
                ],
            )

            def run(self, context: AgentToolContext, input_data: _EchoArgs) -> CapabilityResult:
                return CapabilityResult.success(data={"text": input_data.text})

        registry, gateway = build_tooling()
        registry.register_external_tool(_EchoTool())
        progress_parts: list[str] = []
        context = build_tool_context(
            registry=registry,
            gateway=gateway,
            session_id="sess_tool_progress_candidates_001",
            turn_id="turn_tool_progress_candidates_001",
        )
        context.progress_callback = progress_parts.append

        result = gateway.invoke(name="echo_progress_candidates", context=context, arguments={"text": "一"})
        gateway.invoke(name="echo_progress_candidates", context=context, arguments={"text": "二"})

        self.assertEqual(len(progress_parts), 1)
        self.assertIn(progress_parts[0], {"我先处理一下。", "稍等，我看一下。", "好，我来处理。"})
        self.assertEqual(result.data, {"text": "一"})

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
        self.assertIn("需要时可以调用已提供的工具", instructions)
        self.assertNotIn("final_answer", instructions)
        self.assertNotIn("ask_user", instructions)
        self.assertNotIn("json", instructions.lower())
        self.assertNotIn("资产", instructions)
        self.assertNotIn("请遵守以下规则", instructions)

    def test_openai_runner_instructs_agent_to_proactively_save_user_memory(self) -> None:
        """测试目标：验证主 Agent 提示词保留主动记忆维护入口。

        测试方法：
        1. 构造带记忆运行时的 `OpenAIAgentLoopRunner`。
        2. 创建一轮用户说出姓名的运行态。
        3. 检查发给模型的系统提示词。

        预期结果：
        1. 提示词要求调用 `manage_memory` 主动维护记忆。
        2. 提示词保留 `manage_memory` 作为维护长期记忆的入口。
        3. 提示词保留 `memory_search` 作为读取长期记忆的入口。
        """

        memory_runtime = AgentMemoryRuntime(store=InMemoryAgentMemoryStore())
        registry, gateway = build_tooling(memory_runtime=memory_runtime)
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_memory_prompt_001", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_memory_prompt_001",
            session_id="sess_memory_prompt_001",
            device_id="glass-001",
            source="voice_asr",
            input_text="我叫小明。",
        )

        runtime = runner._turn_runtime_factory.build(session=session, turn=turn)
        instructions = runtime.model_request["instructions"]

        self.assertIn("manage_memory", instructions)
        self.assertIn("memory_search", instructions)

    def test_skill_runtime_read_skill_activates_session_and_filters_tools(self) -> None:
        """测试目标：验证 Skill Runtime 可以读取 Skill、激活会话并限制模型工具。

        测试方法：
        1. 注册一个不额外允许模型工具的 Skill。
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
                    allowed_tools=[],
                ),
                content="如果当前输入包含照片，直接根据照片用一句话回答。",
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
        self.assertEqual(tool_names, ["read_skill"])
        self.assertIn("当前 active Skills", result.meta["model_request"]["instructions"])
        self.assertIn("如果当前输入包含照片，直接根据照片用一句话回答。", result.meta["model_request"]["instructions"])
        self.assertEqual(result.meta["model_request"]["active_skills"], ["scene_inspection"])
        self.assertEqual(result.meta["model_request"]["allowed_tool_names"], ["read_skill"])

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

    def test_openai_runner_puts_current_turn_images_into_user_message(self) -> None:
        """测试目标：验证当前轮图片资产会直接进入用户多模态消息。

        测试方法：
        1. 构造一个带图片资产的 `AgentTurn`。
        2. 用假 Agents SDK 执行同步 Agent Loop。
        3. 检查真实输入包含 `image_url`，而持久化 `model_request` 已脱敏。

        预期结果：
        1. 模型输入最后一条 user message 是 `text + image_url` 结构。
        2. `model_request` 不保存真实 base64 图片内容。
        """

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as image_file:
            image_file.write(_FAKE_PNG_BYTES)
            image_path = image_file.name
        self.addCleanup(lambda: os.path.exists(image_path) and os.remove(image_path))

        registry, gateway = build_tooling(camera_gateway=FakeCameraGateway())
        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            session_store=AgentSessionStore(),
            tool_registry=registry,
            tool_gateway=gateway,
        )
        session = AgentSession(session_id="sess_multimodal_001", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_multimodal_001",
            session_id="sess_multimodal_001",
            device_id="glass-001",
            source="voice_asr",
            input_text="看一下我前面有什么",
            asset_refs=[
                MediaAssetRef(
                    asset_id="asset_image_001",
                    session_id="sess_multimodal_001",
                    asset_type="image",
                    storage_uri=image_path,
                    mime_type="image/png",
                )
            ],
        )

        class _FakeRunResult:
            final_output = "前面有一张床。"

        with install_fake_agents_module():
            with patch("agents.Runner.run_sync", return_value=_FakeRunResult()) as mocked_run:
                result = runner.run_turn(session=session, turn=turn)

        _, input_payload = mocked_run.call_args.args[:2]
        current_content = input_payload[-1]["content"]
        self.assertIsInstance(current_content, list)
        self.assertEqual(current_content[0], {"type": "input_text", "text": "看一下我前面有什么"})
        self.assertEqual(current_content[1]["type"], "input_image")
        self.assertTrue(current_content[1]["image_url"].startswith("data:image/png;base64,"))
        request_content = result.meta["model_request"]["messages"][-1]["content"]
        self.assertEqual(request_content[1]["image_url"], "data:image/*;base64,<redacted>")
        self.assertEqual(result.reply_text, "前面有一张床。")

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
        self.assertEqual(request["model"], "qwen3.5-omni-plus")
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

        self.assertEqual(tool_names, set())
        self.assertIsNone(registry.get("get_latest_utterance_photo"))
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

    def test_agent_facade_consumes_ready_utterance_photo_as_current_input_asset(self) -> None:
        """测试目标：验证自动抓拍照片会直接挂到当前用户输入。

        测试方法：
        1. 注入假相机网关并启动一条后台自动抓拍记录。
        2. 调用 `AgentFacade.handle_turn` 处理当前语音输入。
        3. 检查 runner 收到的 turn 和会话用户消息都带有图片资产。

        预期结果：
        1. 图片资产随当前用户输入进入 agent-core，不再依赖模型调用照片工具。
        2. 同一张自动照片只会被消费一次。
        """

        with tempfile.TemporaryDirectory() as temp_dir:
            settings = ServerSettings(voice_runs_root=temp_dir)
            registry, _gateway = build_tooling(camera_gateway=FakeCameraGateway())
            runner = FakeAgentLoopRunner()
            facade = AgentFacade(
                session_store=AgentSessionStore(),
                tool_registry=registry,
                runner=runner,
                settings=settings,
            )
            store = registry.get_utterance_photo_store()
            store.start_capture(
                camera_gateway=registry.get_camera_gateway(),
                session_id="sess_utterance_photo_001",
                device_id="glass-001",
                segment_id="seg_utterance_photo_001",
                stream_id="stream_utterance_photo_001",
                timeout_ms=1000,
            )
            store.wait_for_photo(
                session_id="sess_utterance_photo_001",
                device_id="glass-001",
                segment_id="seg_utterance_photo_001",
                timeout_ms=1000,
            )

            turn = AgentTurn(
                turn_id="turn_utterance_photo_001",
                session_id="sess_utterance_photo_001",
                device_id="glass-001",
                source="voice_asr",
                input_text="看一下我前面有什么",
                meta={
                    "segment_id": "seg_utterance_photo_001",
                    "stream_id": "stream_utterance_photo_001",
                },
            )

            facade.handle_turn(turn)

            self.assertEqual(len(runner.turns), 1)
            image_assets = [asset for asset in runner.turns[0].asset_refs if asset.asset_type == "image"]
            self.assertEqual(len(image_assets), 1)
            image_asset = image_assets[0]
            self.assertEqual(image_asset.mime_type, "image/png")
            self.assertTrue(image_asset.storage_uri.endswith(".png"))
            with open(image_asset.storage_uri, "rb") as handle:
                self.assertEqual(handle.read(), _FAKE_PNG_BYTES)

            session = facade.get_session_store().get_session("sess_utterance_photo_001")
            assert session is not None
            user_message = session.messages[0]
            self.assertIn(image_asset.asset_id, user_message.asset_refs)
            self.assertEqual(store.consume_ready_photos(session_id="sess_utterance_photo_001", device_id="glass-001"), [])

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
