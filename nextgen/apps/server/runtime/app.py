"""服务器端运行时应用实现。"""

from dataclasses import dataclass
from typing import Any, Dict

from nextgen.apps.server.agent.agent_center import AgentCenter
from nextgen.apps.server.gateway.server_gateway import ServerGateway
from nextgen.apps.server.mcp.mcp_registry import ServerMcpRegistry
from nextgen.apps.server.runtime.event_router import EventRouter
from nextgen.apps.server.skills.create_hybrid_task import CreateHybridTaskSkill
from nextgen.apps.server.skills.skills_registry import ServerSkillRegistry
from nextgen.apps.server.storage.state_log_store import StateLogStore
from nextgen.apps.server.task_center.background_task_center import BackgroundTaskCenter

@dataclass
class ServerRuntimeApp:
    """服务器端运行时应用。"""

    name: str = "server-runtime"

    def start(self) -> None:
        """启动服务器端运行时。

        主要逻辑：
        - 当前阶段完成最小模块装配，便于后续扩展真实服务启动逻辑。
        """

        self.gateway = ServerGateway()
        self.event_router = EventRouter()
        self.agent_center = AgentCenter()
        self.background_task_center = BackgroundTaskCenter()
        self.skill_registry = ServerSkillRegistry()
        self.state_log_store = StateLogStore()
        self.create_hybrid_task = CreateHybridTaskSkill(
            task_center=self.background_task_center,
            state_log_store=self.state_log_store,
        )
        self.mcp_registry = ServerMcpRegistry()
        self.agent_center.task_center = self.background_task_center

        self.skill_registry.register("create_hybrid_task", self.create_hybrid_task)
        self.event_router.on_keyword_dispatch = self.handle_keyword_dispatch

    def stop(self) -> None:
        """停止服务器端运行时。"""

        self.gateway.disconnect()

    def handle_keyword_dispatch(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """处理基于关键词的任务分发。"""

        payload = event.get("payload", {})
        text = payload.get("text", "")
        parsed = self.agent_center.interpret(text)
        if parsed.get("intent") == "create_hybrid_task":
            return self.create_hybrid_task.run(
                task_name=parsed["task_name"],
                params=parsed["params"],
            )
        return {"status": "ignored", "reason": "no_matching_intent"}
