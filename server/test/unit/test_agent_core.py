"""Phase D agent-core 单元测试。"""

from __future__ import annotations

import asyncio
import threading
import unittest
from unittest.mock import patch

from agent_core import AgentFacade, AgentTurn, AgentTurnResult, DerivedArtifact, MediaAssetRef
from agent_core.context import AgentSession, AgentSessionStore, CapabilityTrace, MessageContext
from agent_core.context.assembler import ContextAssembler
from agent_core.runtime import AgentLoopRunner, OpenAIAgentLoopRunner
from agent_core.runtime.runner import StructuredAgentReply
from agent_core.tools import AgentToolContext, ToolRegistry
from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error


class FakeAgentLoopRunner(AgentLoopRunner):
    """测试用假运行循环。"""

    def __init__(self) -> None:
        self.turns: list[AgentTurn] = []

    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
        self.turns.append(turn)
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            action="final_answer",
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

    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
        raise build_error(
            ErrorCode.INTERNAL_ERROR,
            "模拟 agent-core 失败",
        )


class AskUserAgentLoopRunner(AgentLoopRunner):
    """测试用追问运行循环。"""

    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            action="ask_user",
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
        tool_registry = ToolRegistry(device_state_reader=lambda: {})
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
        1. 返回结果动作为 `fail`。
        2. 回复文本中包含失败原因。
        """

        facade = AgentFacade(
            session_store=AgentSessionStore(),
            tool_registry=ToolRegistry(device_state_reader=lambda: {}),
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

        self.assertEqual(result.action, "fail")
        self.assertIn("模拟 agent-core 失败", result.reply_text)
        session = facade.get_session_store().get_session("sess_002")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(len(session.messages), 2)
        self.assertEqual(session.messages[1].meta["action"], "fail")

    def test_agent_facade_persists_ask_user_and_dialog_state(self) -> None:
        """测试目标：验证 ask_user 结果会写入消息并更新 pending_question。"""

        facade = AgentFacade(
            session_store=AgentSessionStore(),
            tool_registry=ToolRegistry(device_state_reader=lambda: {}),
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

        self.assertEqual(result.action, "ask_user")
        session = facade.get_session_store().get_session("sess_ask_001")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session.messages[1].kind, "assistant_question")
        self.assertEqual(session.dialog_state.pending_question, "你想让我查设备状态，还是直接回答问题？")

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
        registry = ToolRegistry(
            device_state_reader=lambda: {
                "glass-001": {
                    "session_id": "sess_003",
                    "state": "listening",
                    "audio_connection_online": True,
                    "reply_stream_id": None,
                }
            }
        )

        result = registry.invoke(
            name="query_device_state",
            context=AgentToolContext(
                session_id="sess_003",
                device_id="glass-001",
                turn_id="turn_003",
                device_state_reader=registry.get_device_state_reader(),
                trace_sink=traces.append,
            ),
        )

        self.assertEqual(result["state"], "listening")
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
        3. 检查传给 SDK 的输入是否包含历史消息。

        预期结果：
        1. `Runner.run_sync` 被调用一次。
        2. 返回值被转换为统一 `AgentTurnResult`。
        3. 输入文本中包含最近历史消息。
        """

        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            tool_registry=ToolRegistry(device_state_reader=lambda: {}),
        )
        session = AgentSession(
            session_id="sess_004",
            device_id="glass-001",
            messages=[
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
                self.final_output = StructuredAgentReply(
                    action="final_answer",
                    reply_text="设备当前正在监听",
                )

        with patch("agents.Runner.run_sync", return_value=_FakeRunResult()) as mocked_run:
            result = runner.run_turn(session=session, turn=turn)

        self.assertEqual(result.action, "final_answer")
        self.assertEqual(result.reply_text, "设备当前正在监听")
        mocked_run.assert_called_once()
        _, input_payload = mocked_run.call_args.args[:2]
        self.assertIn("上一轮回复", input_payload)
        self.assertIn("把上一轮回复复述给我", input_payload)

    def test_openai_runner_creates_event_loop_in_worker_thread(self) -> None:
        """测试目标：验证 OpenAIAgentLoopRunner 在工作线程中会自动补齐 event loop。

        测试方法：
        1. 在子线程中执行 `runner.run_turn`。
        2. 用假 `Runner.run_sync` 在调用点读取当前线程 event loop。

        预期结果：
        1. 子线程内不会因为缺少 event loop 抛错。
        2. 假 `Runner.run_sync` 能读取到未关闭的 event loop。
        """

        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            tool_registry=ToolRegistry(device_state_reader=lambda: {}),
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
                self.final_output = StructuredAgentReply(
                    action="final_answer",
                    reply_text="收到",
                )

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

        with patch("agents.Runner.run_sync", side_effect=_fake_run_sync):
            thread = threading.Thread(target=_worker, name="agent-test-worker")
            thread.start()
            thread.join(timeout=3)

        self.assertFalse(failure)
        self.assertIn("loop", observed)
        self.assertFalse(observed["closed"])

    def test_structured_agent_reply_accepts_single_item_list(self) -> None:
        """测试目标：验证结构化输出能兼容单元素数组包装。"""

        reply = StructuredAgentReply.model_validate(
            [
                {
                    "action": "final_answer",
                    "reply_text": "这是一个笑话",
                }
            ]
        )

        self.assertEqual(reply.action, "final_answer")
        self.assertEqual(reply.reply_text, "这是一个笑话")

    def test_openai_runner_forces_device_state_tool_for_status_query(self) -> None:
        """测试目标：验证设备状态问题会直接命中 query_device_state。"""

        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            tool_registry=ToolRegistry(
                device_state_reader=lambda: {
                    "glass-001": {
                        "session_id": "sess_006",
                        "state": "listening",
                        "audio_connection_online": True,
                        "reply_stream_id": None,
                    }
                }
            ),
        )
        session = AgentSession(session_id="sess_006", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_006",
            session_id="sess_006",
            device_id="glass-001",
            source="voice_asr",
            input_text="我的眼镜现在怎么样了？",
        )

        result = runner.run_turn(session=session, turn=turn)

        self.assertEqual(result.action, "final_answer")
        self.assertIn("待命监听", result.reply_text)
        self.assertEqual(len(result.capability_traces), 1)
        self.assertEqual(result.capability_traces[0].capability_name, "query_device_state")

    def test_openai_runner_returns_fail_with_trace_when_direct_tool_fails(self) -> None:
        """测试目标：验证直连 Tool 失败时会回到对话链路并保留失败轨迹。"""

        runner = OpenAIAgentLoopRunner(
            settings=ServerSettings(dashscope_api_key="demo-key"),
            tool_registry=ToolRegistry(device_state_reader=lambda: {}),
        )
        session = AgentSession(session_id="sess_007", device_id="glass-001")
        turn = AgentTurn(
            turn_id="turn_007",
            session_id="sess_007",
            device_id="glass-001",
            source="voice_asr",
            input_text="我的眼镜现在怎么样了？",
        )

        result = runner.run_turn(session=session, turn=turn)

        self.assertEqual(result.action, "fail")
        self.assertIn("目标设备当前不在线或状态未知", result.reply_text)
        self.assertEqual(len(result.capability_traces), 1)
        self.assertEqual(result.capability_traces[0].status, "failed")


if __name__ == "__main__":
    unittest.main()
