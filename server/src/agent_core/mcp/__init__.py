"""MCP 层导出。"""

__all__ = ["BaseMcpAdapter", "McpGateway", "McpRegistry"]


def __getattr__(name: str):
    if name == "BaseMcpAdapter":
        from agent_core.mcp.base import BaseMcpAdapter

        return BaseMcpAdapter
    if name == "McpGateway":
        from agent_core.mcp.gateway import McpGateway

        return McpGateway
    if name == "McpRegistry":
        from agent_core.mcp.registry import McpRegistry

        return McpRegistry
    raise AttributeError(name)

