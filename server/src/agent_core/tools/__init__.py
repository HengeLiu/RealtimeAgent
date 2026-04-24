"""agent-core 工具层导出。"""

__all__ = ["AgentToolContext", "BaseTool", "ToolGateway", "ToolRegistry"]


def __getattr__(name: str):
    if name in {"AgentToolContext", "BaseTool"}:
        from agent_core.tools.base import AgentToolContext, BaseTool

        return {
            "AgentToolContext": AgentToolContext,
            "BaseTool": BaseTool,
        }[name]
    if name == "ToolGateway":
        from agent_core.tools.gateway import ToolGateway

        return ToolGateway
    if name == "ToolRegistry":
        from agent_core.tools.registry import ToolRegistry

        return ToolRegistry
    raise AttributeError(name)

