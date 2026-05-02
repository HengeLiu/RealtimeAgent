"""外部 MCP Server 客户端适配器。"""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from pydantic import BaseModel, ConfigDict, Field, create_model

from agent_core.mcp.base import BaseMcpAdapter
from agent_core.models import CapabilityResult, McpMethodSpec
from agent_core.tools.base import AgentToolContext


@dataclass(slots=True)
class ExternalMcpServerConfig:
    """外部 MCP Server 连接配置。

    主要功能：
    1. 描述一个可由 SDK 连接的官方或第三方 MCP Server。
    2. 支持 stdio、SSE 和 Streamable HTTP 三类常见传输。
    3. 让业务侧只配置外部 MCP Server，不再被迫手写 `BaseMcpAdapter` 包装 Web API。

    主要属性：
    1. `name`：SDK 内部 adapter 名称。
    2. `transport`：连接方式，支持 `stdio`、`sse`、`streamable_http`。
    3. `command/args/env/cwd`：stdio 进程启动参数。
    4. `url/headers`：SSE 或 Streamable HTTP 连接参数。
    5. `method_prefix`：可选方法名前缀，避免多个 MCP Server 工具重名。
    """

    name: str
    transport: str
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] | None = None
    method_prefix: str = ""
    connect_timeout_seconds: float = 30.0


class ExternalMcpAdapter(BaseMcpAdapter):
    """基于官方 MCP Python SDK 的外部 MCP client adapter。

    主要功能：
    1. 连接业务配置的 stdio/SSE/Streamable HTTP MCP Server。
    2. 自动把外部 Server 的 tools 映射为 SDK `McpMethodSpec`。
    3. 通过 `context.mcp(...)` 和内部 `McpGateway` 统一调用外部工具。

    主要方法：
    1. `list_methods`：连接外部 MCP Server 并读取工具清单。
    2. `invoke`：调用外部 MCP tool 并返回 `CapabilityResult`。
    """

    def __init__(self, config: ExternalMcpServerConfig) -> None:
        """初始化外部 MCP adapter。

        参数：
        1. `config`：外部 MCP Server 连接配置。

        异常情况：
        1. 配置缺少名称、传输方式或必要连接参数时抛出 `ValueError`。
        """

        self.config = config
        self.adapter_name = config.name.strip()
        if not self.adapter_name:
            raise ValueError("外部 MCP adapter name 不能为空")
        self._transport = config.transport.strip().lower().replace("-", "_")
        if self._transport not in {"stdio", "sse", "streamable_http"}:
            raise ValueError(f"不支持的 MCP transport: {config.transport}")
        if self._transport == "stdio" and not str(config.command or "").strip():
            raise ValueError("stdio MCP Server 必须配置 command")
        if self._transport in {"sse", "streamable_http"} and not str(config.url or "").strip():
            raise ValueError(f"{config.transport} MCP Server 必须配置 url")
        self._method_prefix = config.method_prefix.strip()
        self._method_to_tool_name: dict[str, str] = {}
        self._specs: list[McpMethodSpec] | None = None

    def list_methods(self) -> list[McpMethodSpec]:
        """读取外部 MCP Server 的工具清单。

        返回值：
        1. `McpMethodSpec` 列表，方法名会按 `method_prefix` 处理。

        异常情况：
        1. 未安装 `mcp` 依赖、Server 启动失败或 tools/list 失败时抛出异常。
        """

        if self._specs is None:
            self._specs = _run_async_blocking(self._list_methods_async())
        return list(self._specs)

    def invoke(self, *, method_name: str, context: AgentToolContext, input_data: BaseModel) -> CapabilityResult:
        """调用外部 MCP tool。

        参数：
        1. `method_name`：SDK 内部 MCP 方法名。
        2. `context`：能力调用上下文。
        3. `input_data`：已经由 SDK 校验过的工具入参模型。

        返回值：
        1. `CapabilityResult`。MCP tool 返回错误时会转换为失败结果。

        异常情况：
        1. 连接、调用或返回解析异常会向上抛出，由 `McpGateway` 统一包装。
        """

        arguments = input_data.model_dump(exclude_none=True)
        return _run_async_blocking(self._invoke_async(method_name=method_name, arguments=arguments))

    async def _list_methods_async(self) -> list[McpMethodSpec]:
        """异步读取外部 MCP tools/list 结果。"""

        async with self._client_session() as session:
            tools_result = await session.list_tools()
        tools = list(getattr(tools_result, "tools", []) or [])
        specs: list[McpMethodSpec] = []
        self._method_to_tool_name.clear()
        for tool in tools:
            tool_name = str(_read_attr_or_key(tool, "name") or "").strip()
            if not tool_name:
                continue
            method_name = self._build_method_name(tool_name)
            description = str(_read_attr_or_key(tool, "description") or f"外部 MCP 工具 {tool_name}")
            input_schema = _read_attr_or_key(tool, "inputSchema") or _read_attr_or_key(tool, "input_schema") or {}
            input_model = _build_input_model(method_name=method_name, schema=input_schema)
            self._method_to_tool_name[method_name] = tool_name
            specs.append(
                McpMethodSpec(
                    name=method_name,
                    description=description,
                    input_model=input_model,
                )
            )
        return specs

    async def _invoke_async(self, *, method_name: str, arguments: dict[str, Any]) -> CapabilityResult:
        """异步调用外部 MCP tool。"""

        if self._specs is None:
            self._specs = await self._list_methods_async()
        tool_name = self._method_to_tool_name.get(method_name)
        if not tool_name:
            raise RuntimeError(f"外部 MCP 方法不存在: {method_name}")
        async with self._client_session() as session:
            result = await session.call_tool(tool_name, arguments)
        if bool(_read_attr_or_key(result, "isError") or _read_attr_or_key(result, "is_error") or False):
            return CapabilityResult.failed(
                code="MCP_TOOL_ERROR",
                message=f"外部 MCP 工具调用失败: {method_name}",
                details={"method_name": method_name, "content": _serialize_mcp_content(_read_attr_or_key(result, "content"))},
            )
        return CapabilityResult.success(
            data={
                "method_name": method_name,
                "tool_name": tool_name,
                "content": _serialize_mcp_content(_read_attr_or_key(result, "content")),
                "structured_content": _read_attr_or_key(result, "structuredContent")
                or _read_attr_or_key(result, "structured_content"),
            },
            message=f"外部 MCP 工具调用完成: {method_name}",
        )

    @asynccontextmanager
    async def _client_session(self) -> AsyncIterator[Any]:
        """创建一次 MCP client session。"""

        try:
            from mcp import ClientSession
        except ImportError as exc:
            raise RuntimeError("缺少 mcp 依赖，请安装 openaiglasses-sdk[mcp] 或手动安装 mcp") from exc

        if self._transport == "stdio":
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            params = StdioServerParameters(
                command=str(self.config.command),
                args=list(self.config.args),
                env=dict(self.config.env) if self.config.env is not None else None,
                cwd=self.config.cwd,
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
            return

        if self._transport == "sse":
            from mcp.client.sse import sse_client

            async with sse_client(
                url=str(self.config.url),
                headers=dict(self.config.headers or {}),
                timeout=self.config.connect_timeout_seconds,
            ) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    yield session
            return

        from mcp.client.streamable_http import streamable_http_client

        async with streamable_http_client(
            str(self.config.url),
            headers=dict(self.config.headers or {}),
            timeout=self.config.connect_timeout_seconds,
        ) as streams:
            read_stream = streams[0]
            write_stream = streams[1]
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                yield session

    def _build_method_name(self, tool_name: str) -> str:
        """根据配置生成 SDK 内部 MCP 方法名。"""

        if not self._method_prefix:
            return tool_name
        return f"{self._method_prefix}.{tool_name}"


def _run_async_blocking(coro):
    """在同步 SDK 接口中运行异步 MCP 调用。"""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_box: list[Any] = []
    error_box: list[BaseException] = []

    def _runner() -> None:
        try:
            result_box.append(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001 - 需要跨线程回传所有异常
            error_box.append(exc)

    thread = threading.Thread(target=_runner, name="external-mcp-client", daemon=True)
    thread.start()
    thread.join()
    if error_box:
        raise error_box[0]
    return result_box[0] if result_box else None


def _build_input_model(*, method_name: str, schema: Any) -> type[BaseModel]:
    """把 MCP JSON Schema 转成宽松的 Pydantic 输入模型。"""

    properties = dict(schema.get("properties") or {}) if isinstance(schema, dict) else {}
    required = {str(item) for item in (schema.get("required") or [])} if isinstance(schema, dict) else set()
    fields: dict[str, tuple[Any, Any]] = {}
    for name, prop in properties.items():
        if not isinstance(name, str) or not name:
            continue
        description = str(prop.get("description") or "") if isinstance(prop, dict) else ""
        default = ... if name in required else None
        fields[name] = (Any, Field(default=default, description=description))
    if not fields:
        fields["arguments"] = (dict[str, Any], Field(default_factory=dict, description="外部 MCP 工具入参"))
    model_name = "".join(part.capitalize() for part in method_name.replace("-", "_").replace(".", "_").split("_"))
    return create_model(
        f"{model_name or 'ExternalMcp'}Input",
        __config__=ConfigDict(extra="allow"),
        **fields,
    )


def _serialize_mcp_content(content: Any) -> list[dict[str, Any]]:
    """把 MCP content 对象转为 JSON 兼容结构。"""

    if content is None:
        return []
    items = content if isinstance(content, list) else [content]
    serialized: list[dict[str, Any]] = []
    for item in items:
        if hasattr(item, "model_dump"):
            serialized.append(item.model_dump(mode="json", exclude_none=True))
        elif isinstance(item, dict):
            serialized.append(dict(item))
        else:
            serialized.append({"type": type(item).__name__, "text": str(item)})
    return serialized


def _read_attr_or_key(value: Any, name: str) -> Any:
    """兼容对象属性和字典字段读取。"""

    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)
