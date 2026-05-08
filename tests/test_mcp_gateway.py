from __future__ import annotations

import asyncio

import pytest
import yaml

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.errors import ErrorCode
from audio_chat.mcp import McpError, McpGateway, McpToolSpec


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

    result = gateway.call(tool_name="web.search", arguments={"query": "audio-chat"})

    assert result["tool_name"] == "web.search"
    assert result["arguments"]["query"] == "audio-chat"
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
    app = AudioChatApp(
        AudioChatConfig(
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
