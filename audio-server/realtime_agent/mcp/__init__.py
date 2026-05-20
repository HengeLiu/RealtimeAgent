from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

import yaml

from realtime_agent.errors import RealtimeAgentError, ErrorCode


@dataclass(frozen=True)
class McpToolSpec:
    """MCP 工具描述。

    主要功能：记录可由 `McpGateway` 调用的 MCP tool 元数据。
    主要属性：`name` 是调用名，`description` 给模型和调试使用，`parameters`
    是可选 JSON Schema，`target_name` 是远端 MCP server 的真实工具名，
    `mock_result` 用于本地无外部服务验收。
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    mock_result: Any = None
    server: str = "local"
    target_name: str = ""


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
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


class McpError(RealtimeAgentError):
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
        self._local_env: dict[str, str] = {}
        if self.config_path and self.config_path.exists():
            self.load_config(self.config_path)

    def load_config(self, path: str | Path) -> None:
        """读取 MCP 配置。

        参数：`path` 为 yaml 或 json 配置文件。
        返回值：无。
        异常情况：配置缺少 tool name 或格式非法时抛出 `McpError`。
        """

        config_path = Path(path).resolve()
        self._local_env = _load_local_env(config_path)
        self._servers.clear()
        self._tools.clear()
        raw_text = config_path.read_text(encoding="utf-8")
        data = json.loads(raw_text) if config_path.suffix == ".json" else yaml.safe_load(raw_text)
        root = dict(data or {})
        for name, item in _iter_server_items(root):
            spec = McpServerSpec(
                name=name,
                transport=str(item.get("transport") or item.get("type") or "stdio"),
                command=str(item.get("command") or ""),
                args=[str(value) for value in item.get("args", [])],
                url=_expand_env_value(str(item.get("url") or ""), self._local_env),
                headers={str(key): _expand_env_value(str(value), self._local_env) for key, value in dict(item.get("headers") or {}).items()},
                env={str(key): _expand_env_value(str(value), self._local_env) for key, value in dict(item.get("env") or {}).items()},
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
                target_name=str(item.get("target_name") or item.get("remote_name") or "").strip(),
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
                result["headers"] = sorted(spec.headers)
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
        if spec.mock_result is not None:
            return {
                "tool_name": spec.name,
                "server": spec.server,
                "arguments": dict(arguments or {}),
                "result": spec.mock_result,
            }
        if spec.server in self._servers:
            server = self._servers[spec.server]
            external_tool_name = spec.target_name or tool_name
            result = self._call_external_server(
                server=server,
                tool_name=external_tool_name,
                arguments=dict(arguments or {}),
                timeout_seconds=timeout,
            )
            return {
                "tool_name": spec.name,
                "server": spec.server,
                "target_name": external_tool_name,
                "arguments": dict(arguments or {}),
                "result": result,
            }
        return {
            "tool_name": spec.name,
            "server": spec.server,
            "arguments": dict(arguments or {}),
            "result": spec.mock_result if spec.mock_result is not None else {"ok": True},
        }

    def _ensure_enabled(self) -> None:
        if not self.enabled:
            raise McpError("mcp gateway is disabled", code=ErrorCode.PERMISSION_DENIED)

    def _call_external_server(
        self,
        *,
        server: McpServerSpec,
        tool_name: str,
        arguments: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        """调用外部 MCP server。

        主要逻辑：当前实现支持 Streamable HTTP 的 JSON-RPC `initialize`、
        `notifications/initialized` 和 `tools/call` 三步；stdio/SSE 只做配置 smoke。
        参数：`server` 为 MCP server 配置，`tool_name` 和 `arguments` 为外部工具调用。
        返回值：MCP `tools/call` 的 result。
        异常情况：server 未启用、transport 不支持、HTTP 或 JSON-RPC 错误时抛出 `McpError`。
        """

        if not server.enabled:
            raise McpError("mcp server is disabled", code=ErrorCode.PERMISSION_DENIED, details={"server": server.name})
        if server.transport != "streamable_http":
            raise McpError(
                "mcp transport call is not implemented",
                code=ErrorCode.PROTOCOL_ERROR,
                details={"server": server.name, "transport": server.transport},
            )
        session_id = self._initialize_streamable_http(server=server, timeout_seconds=timeout_seconds)
        self._post_streamable_http(
            server=server,
            payload={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            expect_response=False,
        )
        call_response = self._post_streamable_http(
            server=server,
            payload={
                "jsonrpc": "2.0",
                "id": _new_rpc_id("mcp_call"),
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
            timeout_seconds=timeout_seconds,
            session_id=session_id,
            expect_response=True,
        )
        if "error" in call_response:
            raise McpError(
                "mcp tool call failed",
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"server": server.name, "tool_name": tool_name, "error": call_response.get("error")},
            )
        result = call_response.get("result")
        if not isinstance(result, dict):
            raise McpError(
                "mcp tool call returned invalid result",
                code=ErrorCode.PROTOCOL_ERROR,
                details={"server": server.name, "tool_name": tool_name},
            )
        return result

    def _initialize_streamable_http(self, *, server: McpServerSpec, timeout_seconds: float) -> str | None:
        response = self._post_streamable_http(
            server=server,
            payload={
                "jsonrpc": "2.0",
                "id": _new_rpc_id("mcp_init"),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "realtime-agent", "version": "0.1.0"},
                },
            },
            timeout_seconds=timeout_seconds,
            expect_response=True,
        )
        if "error" in response:
            raise McpError(
                "mcp initialize failed",
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"server": server.name, "error": response.get("error")},
            )
        session_id = response.get("_session_id")
        return str(session_id) if session_id else None

    def _post_streamable_http(
        self,
        *,
        server: McpServerSpec,
        payload: dict[str, Any],
        timeout_seconds: float,
        session_id: str | None = None,
        expect_response: bool,
    ) -> dict[str, Any]:
        server = self._server_with_latest_config(server)
        url = server.url.strip()
        if not url:
            raise McpError(
                "mcp server url is required",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"server": server.name},
            )
        if not url.startswith(("http://", "https://")):
            raise McpError(
                "mcp server url must start with http:// or https://",
                code=ErrorCode.INVALID_ARGUMENT,
                details={"server": server.name, "url_prefix": url[:16]},
            )
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            **server.headers,
        }
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        request = urllib_request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib_request.urlopen(request, timeout=timeout_seconds) as response:
                body = response.read().decode("utf-8", errors="replace")
                response_headers = response.headers
        except urllib_error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise McpError(
                "mcp http call failed",
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"server": server.name, "status": exc.code, "body": detail},
            ) from exc
        except urllib_error.URLError as exc:
            raise McpError(
                "mcp http connection failed",
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                details={"server": server.name, "reason": str(exc.reason)},
            ) from exc
        if not expect_response:
            return {}
        decoded = _decode_mcp_http_body(body)
        session_value = response_headers.get("Mcp-Session-Id")
        if session_value:
            decoded["_session_id"] = session_value
        return decoded

    def _server_with_latest_config(self, server: McpServerSpec) -> McpServerSpec:
        """在远程 URL 缺失时重新读取 MCP 配置。

        主要逻辑：服务进程可能先启动，随后才写入 `mcp.local.env`。
        若当前 server URL 为空或不是 HTTP URL，就重新加载配置文件和同目录
        env 文件，避免必须完全重启服务才能拿到本地 MCP 地址。
        参数：`server` 为当前已注册 server。
        返回值：最新配置中的同名 server；找不到时返回原 server。
        异常情况：配置文件不存在或读取失败时保留原错误路径。
        """

        url = server.url.strip()
        if url.startswith(("http://", "https://")):
            return server
        if not self.config_path or not self.config_path.exists():
            return server
        try:
            self.load_config(self.config_path)
        except Exception:
            return server
        return self._servers.get(server.name, server)


def _iter_server_items(config: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """读取常见 MCP 配置格式，提取 server 条目。"""

    servers = config.get("servers")
    if isinstance(servers, list):
        return [(str(item.get("name") or ""), dict(item)) for item in servers if isinstance(item, dict)]
    if isinstance(servers, dict):
        return [(str(name), dict(item or {})) for name, item in servers.items()]
    mcp_servers = config.get("mcpServers")
    if isinstance(mcp_servers, dict):
        return [(str(name), dict(item or {})) for name, item in mcp_servers.items()]
    return []


def _new_rpc_id(prefix: str) -> str:
    return f"{prefix}_{time.time_ns()}"


def _load_local_env(config_path: Path) -> dict[str, str]:
    """读取 MCP 配置旁边的本地 env 文件。

    主要逻辑：按固定文件名从 `mcp.yaml` 同目录读取 `KEY=VALUE` 行，
    系统环境变量仍然优先，避免本地文件意外覆盖启动环境。
    参数：`config_path` 为 MCP 配置文件路径。
    返回值：本地 env 键值表；不存在时返回空字典。
    异常情况：忽略空行、注释和非法行，不因本地 env 文件影响服务启动。
    """

    local_env: dict[str, str] = {}
    for env_path in (config_path.with_suffix(".local.env"), config_path.parent / "mcp.local.env", config_path.parent / ".env", config_path.parent / "local.env"):
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, raw_value = line.split("=", 1)
            key = key.strip()
            if not key:
                continue
            value = raw_value.strip().strip('"').strip("'")
            local_env.setdefault(key, value)
    return local_env


def _env_value(name: str, local_env: dict[str, str]) -> str:
    return os.getenv(name) or local_env.get(name, "")


def _expand_env_value(value: str, local_env: dict[str, str] | None = None) -> str:
    """展开 MCP 配置中的环境变量占位符。"""

    env = local_env or {}
    result = value
    while "${" in result:
        start = result.find("${")
        end = result.find("}", start + 2)
        if end < 0:
            break
        env_name = result[start + 2 : end]
        result = f"{result[:start]}{_env_value(env_name, env)}{result[end + 1:]}"
    stripped = result.strip()
    if stripped.startswith("$") and len(stripped) > 1 and " " not in stripped:
        return _env_value(stripped[1:], env)
    return result


def _decode_mcp_http_body(body: str) -> dict[str, Any]:
    """解析 MCP HTTP 响应，兼容 JSON 和 text/event-stream。"""

    text = body.strip()
    if not text:
        return {}
    if text.startswith("data:") or "\ndata:" in text:
        data_lines = [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise McpError("mcp event stream response has no data", code=ErrorCode.PROTOCOL_ERROR)
        text = data_lines[-1]
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpError(
            "mcp response is not valid json",
            code=ErrorCode.PROTOCOL_ERROR,
            details={"body": body[:500]},
        ) from exc
    if not isinstance(decoded, dict):
        raise McpError("mcp response is not an object", code=ErrorCode.PROTOCOL_ERROR)
    return decoded


__all__ = ["McpError", "McpGateway", "McpServerSpec", "McpToolSpec"]
