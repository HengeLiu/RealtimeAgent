"""导入级冒烟测试。"""

from nextgen.apps.glass.execution.device_control import GlassDeviceControl
from nextgen.apps.glass.execution.executor_bus import GlassExecutorBus
from nextgen.apps.glass.gateway.glass_gateway import GlassGateway
from nextgen.apps.glass.sensors.event_detector import GlassEventDetector
from nextgen.apps.glass.sensors.sensor_hub import GlassSensorHub
from nextgen.apps.server.agent.agent_center import AgentCenter
from nextgen.apps.server.conversation.dashscope_asr_service import DashscopeAsrService
from nextgen.apps.server.conversation.dashscope_chat_service import DashscopeChatService
from nextgen.apps.server.conversation.dashscope_tts_service import DashscopeTtsService
from nextgen.apps.server.conversation.voice_session_manager import VoiceSessionManager
from nextgen.apps.server.gateway.server_gateway import ServerGateway
from nextgen.apps.server.mcp.mcp_registry import ServerMcpRegistry
from nextgen.apps.server.skills.create_hybrid_task import CreateHybridTaskSkill
from nextgen.apps.server.skills.skills_registry import ServerSkillRegistry
from nextgen.apps.server.storage.state_log_store import StateLogStore
from nextgen.apps.server.task_center.background_task_center import BackgroundTaskCenter


def test_all_stage_one_skeleton_classes_can_be_imported() -> None:
    """验证第一阶段核心骨架类可以正常导入和实例化。"""

    instances = [
        GlassGateway(),
        GlassSensorHub(),
        GlassEventDetector(device_id="glass-001"),
        GlassExecutorBus(),
        GlassDeviceControl(),
        ServerGateway(),
        AgentCenter(),
        DashscopeAsrService(api_key="test"),
        DashscopeTtsService(api_key="test"),
        DashscopeChatService(api_key="test"),
        BackgroundTaskCenter(),
        ServerSkillRegistry(),
        CreateHybridTaskSkill(),
        ServerMcpRegistry(),
        StateLogStore(),
        VoiceSessionManager(
            asr_service=object(),
            tts_service=object(),
            chat_service=object(),
            agent_center=AgentCenter(),
        ),
    ]
    assert len(instances) == 16
