from __future__ import annotations

import asyncio
import json

import pytest
import yaml

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.errors import ErrorCode
from realtime_agent.mcp import McpError, McpGateway, McpToolSpec


def test_mcp_gateway_loads_config_and_calls_mock_tool(tmp_path) -> None:
    """测试目标：验证 MCP Gateway 的配置读取和调用语义。

    测试方法：写入一个本地 MCP yaml 配置，加载后调用其中的 mock tool。
    预期结果：调用结果包含 tool_name、arguments 和配置中的 mock_result。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "name": "web.search",
                        "description": "搜索网页",
                        "mock_result": {"items": [{"title": "结果"}]},
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    gateway = McpGateway(enabled=True, config_path=config_path)

    result = gateway.call(tool_name="web.search", arguments={"query": "realtime-agent"})

    assert result["tool_name"] == "web.search"
    assert result["arguments"]["query"] == "realtime-agent"
    assert result["result"]["items"][0]["title"] == "结果"


def test_mcp_gateway_reports_disabled_and_timeout_errors() -> None:
    """测试目标：验证 MCP Gateway 的结构化错误。

    测试方法：分别在未启用状态和零超时状态调用 MCP tool。
    预期结果：返回 permission_denied 和 timeout 错误码。
    """

    disabled = McpGateway(enabled=False)
    with pytest.raises(McpError) as disabled_exc:
        disabled.call(tool_name="demo")
    assert disabled_exc.value.code == ErrorCode.PERMISSION_DENIED

    gateway = McpGateway(enabled=True)
    gateway.register_tool(McpToolSpec(name="demo"))
    with pytest.raises(McpError) as timeout_exc:
        gateway.call(tool_name="demo", timeout_seconds=0)
    assert timeout_exc.value.code == ErrorCode.TIMEOUT


def test_mcp_call_tool_is_exposed_and_uses_gateway(tmp_path) -> None:
    """测试目标：验证 mcp.enabled=true 时内置 mcp_call 可被 Agent 调用。

    测试方法：配置 MCP mock tool，使用 ToolGateway 调用 mcp_call。
    预期结果：ToolResult 成功，并返回 Gateway 的结构化结果。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump({"tools": [{"name": "map.route", "mock_result": {"distance": 120}}]}, allow_unicode=True),
        encoding="utf-8",
    )
    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            mcp_enabled=True,
            mcp_config_path=str(config_path),
        )
    )

    result = asyncio.run(
        app.tool_gateway.call(
            name="mcp_call",
            user_id="user-mcp",
            session_id="session-mcp",
            input_data={"tool_name": "map.route", "arguments": {"to": "office"}},
        )
    )

    assert result.ok is True
    assert result.data["result"]["distance"] == 120


def test_mcp_gateway_calls_streamable_http_tool(tmp_path, monkeypatch) -> None:
    """测试目标：验证 Gateway 可以通过 Streamable HTTP 调用外部 MCP tool。

    测试方法：写入一个 streamable_http server，把本地 `amap.route_plan`
    映射到远端 `maps_direction_walking`，monkeypatch HTTP 层模拟
    initialize、initialized notification 和 tools/call。
    预期结果：Gateway 返回外部 MCP 的 route result，HTTP 请求携带 Bearer
    Token，并使用映射后的远端工具名。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "servers": [
                    {
                        "name": "amap",
                        "transport": "streamable_http",
                        "url": "${AMAP_MCP_URL}",
                        "headers": {"Authorization": "Bearer ${AMAP_MCP_BEARER_TOKEN}"},
                    }
                ],
                "tools": [{"name": "amap.route_plan", "server": "amap", "target_name": "maps_direction_walking"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeHeaders(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class FakeResponse:
        def __init__(self, body: dict, headers: dict | None = None) -> None:
            self._body = body
            self.headers = FakeHeaders(headers or {})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(self._body, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append({"payload": payload, "headers": dict(request.header_items()), "timeout": timeout})
        if payload.get("method") == "initialize":
            return FakeResponse({"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}}, {"Mcp-Session-Id": "session-1"})
        if payload.get("method") == "notifications/initialized":
            return FakeResponse({})
        return FakeResponse(
            {
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"type": "text", "text": "路线已规划"}]},
            }
        )

    monkeypatch.setenv("AMAP_MCP_URL", "https://mcp.example.com/mcp")
    monkeypatch.setenv("AMAP_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setattr("realtime_agent.mcp.urllib_request.urlopen", fake_urlopen)
    gateway = McpGateway(enabled=True, config_path=config_path)

    result = gateway.call(tool_name="amap.route_plan", arguments={"origin": "家", "destination": "地铁站"}, timeout_seconds=3)

    assert result["tool_name"] == "amap.route_plan"
    assert result["target_name"] == "maps_direction_walking"
    assert result["server"] == "amap"
    assert result["arguments"]["destination"] == "地铁站"
    assert result["result"]["content"][0]["text"] == "路线已规划"
    assert [call["payload"]["method"] for call in calls] == ["initialize", "notifications/initialized", "tools/call"]
    assert calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert calls[1]["headers"]["Mcp-session-id"] == "session-1"
    assert calls[2]["payload"]["params"]["name"] == "maps_direction_walking"


def test_mcp_gateway_prepare_reuses_streamable_http_session(tmp_path, monkeypatch) -> None:
    """测试目标：验证 MCP Gateway 可在服务启动阶段预热 Streamable HTTP session。

    测试方法：先调用 `prepare()`，再调用同一 MCP tool，并记录 HTTP JSON-RPC 方法。
    预期结果：prepare 阶段完成 initialize/initialized，正式工具调用只发送 tools/call。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "servers": [{"name": "amap", "transport": "streamable_http", "url": "https://mcp.example.com/mcp"}],
                "tools": [{"name": "amap.route_plan", "server": "amap", "target_name": "maps_direction_walking"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    calls = []

    class FakeHeaders(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class FakeResponse:
        def __init__(self, body: dict, headers: dict | None = None) -> None:
            self._body = body
            self.headers = FakeHeaders(headers or {})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(self._body, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, timeout):
        payload = json.loads(request.data.decode("utf-8"))
        calls.append({"payload": payload, "headers": dict(request.header_items()), "timeout": timeout})
        if payload.get("method") == "initialize":
            return FakeResponse({"jsonrpc": "2.0", "id": payload["id"], "result": {"capabilities": {}}}, {"Mcp-Session-Id": "warm-session"})
        if payload.get("method") == "notifications/initialized":
            return FakeResponse({})
        return FakeResponse({"jsonrpc": "2.0", "id": payload["id"], "result": {"content": [{"type": "text", "text": "ok"}]}})

    monkeypatch.setattr("realtime_agent.mcp.urllib_request.urlopen", fake_urlopen)
    gateway = McpGateway(enabled=True, config_path=config_path)

    prepare_results = gateway.prepare(timeout_seconds=3)
    result = gateway.call(tool_name="amap.route_plan", arguments={"origin": "家", "destination": "地铁站"}, timeout_seconds=3)

    assert prepare_results[0]["ok"] is True
    assert prepare_results[0]["session_ready"] is True
    assert result["result"]["content"][0]["text"] == "ok"
    assert [call["payload"]["method"] for call in calls] == ["initialize", "notifications/initialized", "tools/call"]
    assert calls[-1]["headers"]["Mcp-session-id"] == "warm-session"


def test_mcp_gateway_reports_empty_streamable_http_url(tmp_path, monkeypatch) -> None:
    """测试目标：验证远程 MCP URL 未注入时返回可读错误。

    测试方法：配置 `url: ${AMAP_MCP_URL}`，但不设置环境变量，直接调用工具。
    预期结果：Gateway 抛出明确的 URL 缺失错误，而不是底层 urllib 的
    `unknown url type`。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "servers": [{"name": "amap", "transport": "streamable_http", "url": "${AMAP_MCP_URL}"}],
                "tools": [{"name": "amap.route_plan", "server": "amap", "target_name": "maps_direction_walking"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AMAP_MCP_URL", raising=False)
    gateway = McpGateway(enabled=True, config_path=config_path)

    with pytest.raises(McpError, match="mcp server url is required"):
        gateway.call(tool_name="amap.route_plan", arguments={"origin": "家", "destination": "地铁站"}, timeout_seconds=3)


def test_mcp_gateway_loads_local_env_next_to_config(tmp_path, monkeypatch) -> None:
    """测试目标：验证 MCP 配置可以读取同目录本地 env 文件。

    测试方法：不设置系统环境变量，把远程 URL 和 Bearer Token 写入
    `mcp.local.env`，再加载包含 `${AMAP_MCP_URL}` 的配置。
    预期结果：Gateway smoke 能看到有效 URL，headers 只暴露字段名不泄露密钥。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "servers": [
                    {
                        "name": "amap",
                        "transport": "streamable_http",
                        "url": "${AMAP_MCP_URL}",
                        "headers": {"Authorization": "Bearer ${AMAP_MCP_BEARER_TOKEN}"},
                    }
                ],
                "tools": [{"name": "amap.route_plan", "server": "amap"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (tmp_path / "mcp.local.env").write_text(
        "AMAP_MCP_URL=https://mcp.example.com/mcp\nAMAP_MCP_BEARER_TOKEN=local-token\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AMAP_MCP_URL", raising=False)
    monkeypatch.delenv("AMAP_MCP_BEARER_TOKEN", raising=False)

    gateway = McpGateway(enabled=True, config_path=config_path)
    smoke = gateway.smoke_external_servers()

    assert smoke[0]["ok"] is True
    assert smoke[0]["url"] == "https://mcp.example.com/mcp"
    assert smoke[0]["headers"] == ["Authorization"]


def test_mcp_gateway_reloads_local_env_when_url_was_missing(tmp_path, monkeypatch) -> None:
    """测试目标：验证服务启动后补写本地 env 文件也能恢复 MCP URL。

    测试方法：先在没有 `mcp.local.env` 时创建 Gateway，再写入 env 文件，
    monkeypatch HTTP 层模拟一次完整 Streamable HTTP 调用。
    预期结果：调用前自动重新加载配置，最终请求到补写后的 URL。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "servers": [
                    {
                        "name": "amap",
                        "transport": "streamable_http",
                        "url": "${AMAP_MCP_URL}",
                        "headers": {"Authorization": "Bearer ${AMAP_MCP_BEARER_TOKEN}"},
                    }
                ],
                "tools": [{"name": "amap.route_plan", "server": "amap", "target_name": "maps_direction_walking"}],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("AMAP_MCP_URL", raising=False)
    monkeypatch.delenv("AMAP_MCP_BEARER_TOKEN", raising=False)
    gateway = McpGateway(enabled=True, config_path=config_path)
    (tmp_path / "mcp.local.env").write_text(
        "AMAP_MCP_URL=https://mcp.example.com/mcp\nAMAP_MCP_BEARER_TOKEN=late-token\n",
        encoding="utf-8",
    )
    seen_urls: list[str] = []

    class FakeHeaders(dict):
        def get(self, key, default=None):
            return super().get(key, default)

    class FakeResponse:
        def __init__(self, body: dict, headers: dict | None = None) -> None:
            self._body = body
            self.headers = FakeHeaders(headers or {})

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(self._body, ensure_ascii=False).encode("utf-8")

    def fake_urlopen(request, timeout):
        seen_urls.append(request.full_url)
        payload = json.loads(request.data.decode("utf-8"))
        if payload.get("method") == "initialize":
            return FakeResponse({"jsonrpc": "2.0", "id": payload["id"], "result": {}}, {"Mcp-Session-Id": "session-2"})
        if payload.get("method") == "notifications/initialized":
            return FakeResponse({})
        return FakeResponse({"jsonrpc": "2.0", "id": payload["id"], "result": {"content": []}})

    monkeypatch.setattr("realtime_agent.mcp.urllib_request.urlopen", fake_urlopen)

    result = gateway.call(tool_name="amap.route_plan", arguments={"origin": "家", "destination": "地铁站"}, timeout_seconds=3)

    assert result["target_name"] == "maps_direction_walking"
    assert seen_urls == ["https://mcp.example.com/mcp", "https://mcp.example.com/mcp", "https://mcp.example.com/mcp"]
