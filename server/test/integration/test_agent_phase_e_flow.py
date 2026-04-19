"""Phase E 能力层集成测试。"""

from __future__ import annotations

import tempfile
import unittest

from agent_core import AgentFacade, AgentTurn, AgentTurnResult
from agent_core.camera import CameraCaptureResult
from agent_core.context import AgentSession, AgentSessionStore
from agent_core.runtime import AgentLoopRunner
from agent_core.tools import AgentToolContext, ToolGateway, ToolRegistry
from infra.config import ServerSettings


class FakeCameraGateway:
    """测试用假相机网关。"""

    def capture_photo(self, *, device_id: str, session_id: str, reason: str, timeout_ms: int) -> CameraCaptureResult:
        return CameraCaptureResult(
            request_id="capture_phase_e_001",
            image_bytes=(
                b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
                b"\x08\x04\x00\x00\x00\xb5\x1c\x0c\x02\x00\x00\x00\x0bIDATx\xdac\xfc\xff"
                b"\x1f\x00\x02\xeb\x01\xf5\x8fg?\xed\x00\x00\x00\x00IEND\xaeB`\x82"
            ),
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


class PhaseECapabilityRunner(AgentLoopRunner):
    """测试用能力链路 Runner。"""

    def __init__(self, *, settings: ServerSettings, session_store: AgentSessionStore, tool_registry: ToolRegistry) -> None:
        self._settings = settings
        self._session_store = session_store
        self._tool_registry = tool_registry
        self._tool_gateway = ToolGateway(tool_registry)
        self._tool_registry.bind_gateway(self._tool_gateway)

    def run_turn(self, *, session: AgentSession, turn: AgentTurn) -> AgentTurnResult:
        traces = []
        context = AgentToolContext(
            session_id=turn.session_id,
            device_id=turn.device_id,
            turn_id=turn.turn_id,
            settings=self._settings,
            session_store=self._session_store,
            device_state_reader=self._tool_registry.get_device_state_reader(),
            trace_sink=traces.append,
            task_gateway=self._tool_registry.get_task_gateway(),
            camera_gateway=self._tool_registry.get_camera_gateway(),
            tool_gateway=self._tool_gateway,
            skill_gateway=self._tool_registry.get_skill_gateway(),
            mcp_gateway=self._tool_registry.get_mcp_gateway(),
        )

        photo_result = self._tool_gateway.invoke(
            name="photo_interpret",
            context=context,
            arguments={"question": "帮我看看前面有什么"},
        )
        nav_result = self._tool_gateway.invoke(
            name="map_manage",
            context=context,
            arguments={
                "origin": "当前设备位置",
                "destination": "最近的咖啡店",
                "strategy": "walking",
            },
        )
        return AgentTurnResult(
            turn_id=turn.turn_id,
            session_id=turn.session_id,
            device_id=turn.device_id,
            reply_text=f"{photo_result.data['answer_text']} {nav_result.data['summary']}",
            capability_traces=traces,
            meta={
                "asset_refs": list(context.emitted_assets),
                "derived_artifacts": list(context.emitted_artifacts),
                "task_refs": list(context.emitted_tasks),
            },
        )


class AgentPhaseEFlowTestCase(unittest.TestCase):
    """验证一轮 AgentTurn 能串起 Tool / Skill / MCP。"""

    def test_agent_turn_can_chain_tool_skill_and_mcp(self) -> None:
        temp_dir = tempfile.mkdtemp(prefix="phase-e-agent-")
        settings = ServerSettings(voice_runs_root=temp_dir)
        session_store = AgentSessionStore()
        tool_registry = ToolRegistry(
            device_state_reader=lambda: {},
            camera_gateway=FakeCameraGateway(),
        )
        runner = PhaseECapabilityRunner(
            settings=settings,
            session_store=session_store,
            tool_registry=tool_registry,
        )
        facade = AgentFacade(
            session_store=session_store,
            tool_registry=tool_registry,
            runner=runner,
        )

        result = facade.handle_turn(
            AgentTurn(
                turn_id="turn_phase_e_001",
                session_id="sess_phase_e_001",
                device_id="glass-001",
                source="voice_asr",
                input_text="拍照看看前面，然后导航去最近的咖啡店",
            )
        )

        self.assertIsNone(result.error)
        self.assertEqual(len(result.capability_traces), 4)
        self.assertEqual(result.capability_traces[0].capability_name, "capture_photo")
        self.assertEqual(result.capability_traces[1].capability_name, "photo_interpret")
        self.assertEqual(result.capability_traces[2].capability_name, "amap.route_plan")
        self.assertEqual(result.capability_traces[2].capability_type, "mcp")
        self.assertEqual(result.capability_traces[3].capability_name, "map_manage")
        self.assertEqual(result.capability_traces[3].capability_type, "skill")

        session = session_store.get_session("sess_phase_e_001")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertGreaterEqual(len(session.assets), 1)
        self.assertGreaterEqual(len(session.artifacts), 2)
        self.assertEqual(len(session.messages), 2)
        self.assertGreaterEqual(len(session.messages[1].asset_refs), 1)
        self.assertGreaterEqual(len(session.messages[1].derived_refs), 2)


if __name__ == "__main__":
    unittest.main()
