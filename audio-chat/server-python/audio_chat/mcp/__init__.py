from __future__ import annotations

import json
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


class McpError(AudioChatError):
    """MCP Gateway 结构化异常。"""


class McpGateway:
    """MCP 工具调用网关。

    主要功能：读取本地 MCP 配置，管理 tool 描述并提供统一调用入口。
    主要约束：本类不持有 `UserDeviceContext`；MCP 需要设备能力时必须
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
        for item in dict(data or {}).get("tools", []):
            spec = McpToolSpec(
                name=str(item.get("name") or "").strip(),
                description=str(item.get("description") or ""),
                parameters=dict(item.get("parameters") or {}),
                mock_result=item.get("mock_result"),
            )
            self.register_tool(spec)

    def register_tool(self, spec: McpToolSpec) -> None:
        """注册一个 MCP tool 描述。"""

        if not spec.name:
            raise McpError("mcp tool name is required", code=ErrorCode.INVALID_ARGUMENT)
        if spec.name in self._tools:
            raise McpError("duplicate mcp tool", code=ErrorCode.PROTOCOL_ERROR, details={"name": spec.name})
        self._tools[spec.name] = spec

    def list_tools(self) -> list[McpToolSpec]:
        """列出 MCP tool 描述。"""

        self._ensure_enabled()
        return list(self._tools.values())

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
            "arguments": dict(arguments or {}),
            "result": spec.mock_result if spec.mock_result is not None else {"ok": True},
        }

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise McpError("mcp gateway is disabled", code=ErrorCode.PERMISSION_DENIED)


__all__ = ["McpError", "McpGateway", "McpToolSpec"]
