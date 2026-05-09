from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from audio_chat.errors import AudioChatError, ErrorCode


@dataclass(frozen=True)
class McpToolSpec:
    """MCP 工具描述。

    主要功能：记录可由 `McpGateway` 调用的 MCP tool 元数据。
    主要属性：`name` 是调用名，`description` 给模型和调试使用，`parameters`
    是可选 JSON Schema，`mock_result` 用于本地无外部服务验收。
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    mock_result: Any = None
    server: str = "local"


@dataclass(frozen=True)
class McpServerSpec:
    """MCP 外部 server 描述。

    主要功能：记录 stdio、SSE 和 Streamable HTTP 三类 MCP server 的启动或连接信息。
    主要属性：`transport` 决定 smoke 检查方式，`command`/`url` 分别服务本地进程和远端服务。
    """

    name: str
    transport: str
    command: str = ""
    args: list[str] = field(default_factory=list)
    url: str = ""
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class McpError(AudioChatError):
    """MCP Gateway 结构化异常。"""


class McpGateway:
    """MCP 工具调用网关。

    主要功能：读取本地 MCP 配置，管理 tool 描述并提供统一调用入口。
    主要约束：本类不持有 `ToolDeviceFacade`；MCP 需要设备通讯能力时必须
    暴露为普通 Tool 或 Task 后再由业务代码通过 `context.devices` 完成。
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        config_path: str | Path | None = None,
        default_timeout_seconds: float = 30.0,
    ) -> None:
        self.enabled = enabled
        self.config_path = Path(config_path).resolve() if config_path else None
        self.default_timeout_seconds = default_timeout_seconds
        self._tools: dict[str, McpToolSpec] = {}
        self._servers: dict[str, McpServerSpec] = {}
        if self.config_path and self.config_path.exists():
            self.load_config(self.config_path)

    def load_config(self, path: str | Path) -> None:
        """读取 MCP 配置。

        参数：`path` 为 yaml 或 json 配置文件。
        返回值：无。
        异常情况：配置缺少 tool name 或格式非法时抛出 `McpError`。
        """

        config_path = Path(path).resolve()
        raw_text = config_path.read_text(encoding="utf-8")
        data = json.loads(raw_text) if config_path.suffix == ".json" else yaml.safe_load(raw_text)
        root = dict(data or {})
        for name, item in _iter_server_items(root):
            spec = McpServerSpec(
                name=name,
                transport=str(item.get("transport") or item.get("type") or "stdio"),
                command=str(item.get("command") or ""),
                args=[str(value) for value in item.get("args", [])],
                url=str(item.get("url") or ""),
                env={str(key): str(value) for key, value in dict(item.get("env") or {}).items()},
                enabled=bool(item.get("enabled", True)),
            )
            self.register_server(spec)
        for item in root.get("tools", []):
            spec = McpToolSpec(
                name=str(item.get("name") or "").strip(),
                description=str(item.get("description") or ""),
                parameters=dict(item.get("parameters") or {}),
                mock_result=item.get("mock_result"),
                server=str(item.get("server") or "local"),
            )
            self.register_tool(spec)

    def register_tool(self, spec: McpToolSpec) -> None:
        """注册一个 MCP tool 描述。"""

        if not spec.name:
            raise McpError("mcp tool name is required", code=ErrorCode.INVALID_ARGUMENT)
        if spec.name in self._tools:
            raise McpError("duplicate mcp tool", code=ErrorCode.PROTOCOL_ERROR, details={"name": spec.name})
        self._tools[spec.name] = spec

    def register_server(self, spec: McpServerSpec) -> None:
        """注册一个 MCP 外部 server 描述。"""

        if not spec.name:
            raise McpError("mcp server name is required", code=ErrorCode.INVALID_ARGUMENT)
        if spec.transport not in {"stdio", "sse", "streamable_http"}:
            raise McpError(
                "unsupported mcp transport",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"name": spec.name, "transport": spec.transport},
            )
        if spec.name in self._servers:
            raise McpError("duplicate mcp server", code=ErrorCode.PROTOCOL_ERROR, details={"name": spec.name})
        self._servers[spec.name] = spec

    def list_tools(self) -> list[McpToolSpec]:
        """列出 MCP tool 描述。"""

        self._ensure_enabled()
        return list(self._tools.values())

    def smoke_external_servers(self) -> list[dict[str, Any]]:
        """对外部 MCP server 配置做轻量 smoke。

        主要逻辑：stdio 检查命令是否存在；SSE 和 Streamable HTTP 检查 URL
        是否可解释。本函数不主动建立长连接，避免本地预检因网络抖动阻塞。
        参数：无。
        返回值：每个 server 的结构化检查结果。
        异常情况：MCP 未启用时抛出 `McpError`。
        """

        self._ensure_enabled()
        results: list[dict[str, Any]] = []
        for spec in self._servers.values():
            result = {
                "name": spec.name,
                "transport": spec.transport,
                "enabled": spec.enabled,
                "ok": True,
                "errors": [],
                "degradations": [],
            }
            if not spec.enabled:
                result["degradations"].append("mcp server disabled by config")
                results.append(result)
                continue
            if spec.transport == "stdio":
                if not spec.command:
                    result["ok"] = False
                    result["errors"].append("stdio mcp server command is required")
                elif shutil.which(spec.command) is None and not Path(spec.command).exists():
                    result["ok"] = False
                    result["errors"].append(f"stdio mcp server command not found: {spec.command}")
                result["command"] = spec.command
                result["args"] = list(spec.args)
            else:
                if not spec.url:
                    result["ok"] = False
                    result["errors"].append(f"{spec.transport} mcp server url is required")
                elif not spec.url.startswith(("http://", "https://")):
                    result["ok"] = False
                    result["errors"].append(f"{spec.transport} mcp server url must start with http:// or https://")
                result["url"] = spec.url
            results.append(result)
        return results

    def call(self, *, tool_name: str, arguments: dict[str, Any] | None = None, timeout_seconds: float | None = None) -> dict[str, Any]:
        """调用 MCP tool。

        主要逻辑：第一版只实现本地 mock/config tool，冻结 Gateway、超时和
        错误语义；后续可在这里接 stdio/SSE/Streamable HTTP client。
        参数：`tool_name` 为 MCP tool 名称，`arguments` 为调用参数。
        返回值：可 JSON 序列化的调用结果。
        异常情况：未启用、未知 tool 或超时时抛出 `McpError`。
        """

        self._ensure_enabled()
        started = time.monotonic()
        timeout = timeout_seconds if timeout_seconds is not None else self.default_timeout_seconds
        spec = self._tools.get(tool_name)
        if spec is None:
            raise McpError("mcp tool not found", code=ErrorCode.NOT_FOUND, details={"tool_name": tool_name})
        if timeout <= 0:
            raise McpError("mcp call timeout", code=ErrorCode.TIMEOUT, details={"tool_name": tool_name})
        if time.monotonic() - started > timeout:
            raise McpError("mcp call timeout", code=ErrorCode.TIMEOUT, details={"tool_name": tool_name})
        return {
            "tool_name": spec.name,
            "server": spec.server,
            "arguments": dict(arguments or {}),
            "result": spec.mock_result if spec.mock_result is not None else {"ok": True},
        }

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise McpError("mcp gateway is disabled", code=ErrorCode.PERMISSION_DENIED)


def _iter_server_items(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """兼容常见 MCP 配置格式，提取 server 条目。"""

    servers = config.get("servers")
    if isinstance(servers, list):
        return [(str(item.get("name") or ""), dict(item)) for item in servers if isinstance(item, dict)]
    if isinstance(servers, dict):
        return [(str(name), dict(item or {})) for name, item in servers.items()]
    mcp_servers = config.get("mcpServers")
    if isinstance(mcp_servers, dict):
        return [(str(name), dict(item or {})) for name, item in mcp_servers.items()]
    return []


__all__ = ["McpError", "McpGateway", "McpServerSpec", "McpToolSpec"]
