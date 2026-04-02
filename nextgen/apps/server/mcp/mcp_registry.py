"""MCP 接入中心骨架实现。"""

from typing import Any

from nextgen.shared.contracts.registry import McpRegistry


class ServerMcpRegistry(McpRegistry):
    """服务器端 MCP 接入中心。"""

    def __init__(self) -> None:
        """初始化 MCP 注册中心。"""

        self.services: dict[str, Any] = {}

    def register(self, name: str, service: Any) -> None:
        """注册 MCP 服务。"""

        self.services[name] = service

    def get(self, name: str) -> Any:
        """获取 MCP 服务。"""

        return self.services.get(name)

    def list_names(self) -> list[str]:
        """列出已注册 MCP 服务名称。"""

        return sorted(self.services.keys())
