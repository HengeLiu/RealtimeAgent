from __future__ import annotations

from dataclasses import dataclass

from agent_core.context import ConversationContextStore
from agent_core.model_adapter import BailianQwenOmniAdapter
from agent_core.runtime import AgentRuntime, ResponsePlanner
from agent_core.tool_registry import ToolRegistry, ToolSpec
from api.gateway import WsGateway
from api.handlers import AudioHandler, SystemHandler, TaskHandler
from api.router import MessageRouter
from api.session import BindingRegistry, ConnectionManager, DeviceRegistry
from backend_task_core.event_bus import TaskEventBus
from backend_task_core.manager import TaskContextStore, TaskManager
from backend_task_core.registry import TaskRegistry
from backend_task_core.scheduler import TaskScheduler
from backend_task_core.state_machine import TaskStateMachine
from infra.config import Settings
from infra.logging import create_logger
from mcp.adapters import AMapAdapter
from mcp.base import McpRequest
from mcp.registry import McpRegistry
from protocol.codec import JsonMessageCodec
from skill.base import SkillRequest
from skill.builtin import (
    AudioPlaySkill,
    CameraCaptureSkill,
    DeviceStateQuerySkill,
    PhoneVideoLinkSkill,
    TaskManageSkill,
)
from skill.registry import SkillRegistry
from task.templates import NavigationTask, PhotoInterpretTask, TimerTask


@dataclass(slots=True)
class AppContainer:
    settings: Settings
    device_registry: DeviceRegistry
    binding_registry: BindingRegistry
    connection_manager: ConnectionManager
    message_router: MessageRouter
    gateway: WsGateway
    task_manager: TaskManager
    task_registry: TaskRegistry
    skill_registry: SkillRegistry
    mcp_registry: McpRegistry
    agent_runtime: AgentRuntime



def build_container(settings: Settings | None = None) -> AppContainer:
    settings = settings or Settings.from_env()

    # 1. infra
    logger = create_logger("server", settings.log_level)

    # 2. protocol
    codec = JsonMessageCodec()

    # 3. api
    device_registry = DeviceRegistry()
    binding_registry = BindingRegistry()
    connection_manager = ConnectionManager()
    router = MessageRouter()

    system_handler = SystemHandler(
        settings=settings,
        device_registry=device_registry,
        connection_manager=connection_manager,
        logger=logger,
    )
    audio_handler = AudioHandler()
    task_handler = TaskHandler()

    router.register_domain("system", system_handler.handle)
    router.register_domain("audio", audio_handler.handle)
    router.register_domain("task", task_handler.handle)

    gateway = WsGateway(router=router, connection_manager=connection_manager, codec=codec)

    # 4. skill registry
    task_registry = TaskRegistry()
    task_registry.register(TimerTask.task_type, TimerTask)
    task_registry.register(NavigationTask.task_type, NavigationTask)
    task_registry.register(PhotoInterpretTask.task_type, PhotoInterpretTask)

    task_manager = TaskManager(
        task_registry=task_registry,
        state_machine=TaskStateMachine(),
        scheduler=TaskScheduler(),
        event_bus=TaskEventBus(),
        context_store=TaskContextStore(),
    )

    skill_registry = SkillRegistry()
    skill_registry.register(CameraCaptureSkill())
    skill_registry.register(AudioPlaySkill())
    skill_registry.register(TaskManageSkill(task_manager=task_manager))
    skill_registry.register(PhoneVideoLinkSkill())
    skill_registry.register(DeviceStateQuerySkill(device_registry=device_registry))

    # 5. mcp registry
    mcp_registry = McpRegistry()
    mcp_registry.register(AMapAdapter())

    # 6. agent runtime
    tool_registry = ToolRegistry()
    for skill_meta in skill_registry.list_skills():
        name = str(skill_meta["name"])

        def _build_executor(skill_name: str):
            return lambda args: skill_registry.execute(
                skill_name,
                SkillRequest(trace_id="trace_agent", caller="agent-core", input=args),
            ).__dict__

        tool_registry.register(
            ToolSpec(
                name=name,
                description=str(skill_meta["description"]),
                input_schema=dict(skill_meta["input_schema"]),
                mode=str(skill_meta["mode"]),
                executor=_build_executor(name),
            )
        )

    tool_registry.register(
        ToolSpec(
            name="amap_route_tool",
            description="Route planning via amap MCP",
            input_schema={"type": "object", "properties": {"destination": {"type": "string"}}},
            mode="sync",
            executor=lambda args: mcp_registry.invoke(
                "amap_adapter",
                McpRequest(action="route_plan", params=args),
            ).__dict__,
        )
    )

    agent_runtime = AgentRuntime(
        context_store=ConversationContextStore(),
        model_adapter=BailianQwenOmniAdapter(),
        tool_registry=tool_registry,
        response_planner=ResponsePlanner(),
    )

    return AppContainer(
        settings=settings,
        device_registry=device_registry,
        binding_registry=binding_registry,
        connection_manager=connection_manager,
        message_router=router,
        gateway=gateway,
        task_manager=task_manager,
        task_registry=task_registry,
        skill_registry=skill_registry,
        mcp_registry=mcp_registry,
        agent_runtime=agent_runtime,
    )
