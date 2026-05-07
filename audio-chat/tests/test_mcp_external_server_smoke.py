from __future__ import annotations

import yaml

from audio_chat.mcp import McpGateway


def test_mcp_external_server_smoke_reports_structured_errors(tmp_path) -> None:
    """测试目标：验证 MCP 外部 server 缺依赖或配置不完整时有结构化错误。

    测试方法：写入 stdio、SSE、Streamable HTTP 三类 server 配置，其中 stdio
    使用不存在命令，SSE 缺 URL，HTTP 配置完整。
    预期结果：smoke 结果逐项返回 transport、命令或 URL、ok 状态和错误列表。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "servers": [
                    {"name": "broken_stdio", "transport": "stdio", "command": "audio-chat-mcp-missing"},
                    {"name": "broken_sse", "transport": "sse"},
                    {"name": "http_ok", "transport": "streamable_http", "url": "http://127.0.0.1:9898/mcp"},
                ],
                "tools": [
                    {
                        "name": "web.search",
                        "server": "http_ok",
                        "description": "搜索网页",
                        "mock_result": {"items": []},
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    gateway = McpGateway(enabled=True, config_path=config_path)

    results = {item["name"]: item for item in gateway.smoke_external_servers()}
    call_result = gateway.call(tool_name="web.search", arguments={"query": "audio-chat"})

    assert results["broken_stdio"]["ok"] is False
    assert results["broken_stdio"]["transport"] == "stdio"
    assert "command not found" in results["broken_stdio"]["errors"][0]
    assert results["broken_sse"]["ok"] is False
    assert "url is required" in results["broken_sse"]["errors"][0]
    assert results["http_ok"]["ok"] is True
    assert results["http_ok"]["transport"] == "streamable_http"
    assert call_result["server"] == "http_ok"


def test_mcp_gateway_accepts_common_mcp_servers_mapping(tmp_path) -> None:
    """测试目标：验证 Gateway 能读取常见 `mcpServers` 配置格式。

    测试方法：写入 VSCode/Claude 常见的 mapping 格式，并声明本机一定存在的
    `python` stdio command。
    预期结果：stdio server smoke 通过，便于开发者复用现有 MCP 配置。
    """

    config_path = tmp_path / "mcp.json"
    config_path.write_text(
        '{"mcpServers": {"local": {"transport": "stdio", "command": "python", "args": ["--version"]}}}',
        encoding="utf-8",
    )

    gateway = McpGateway(enabled=True, config_path=config_path)
    results = gateway.smoke_external_servers()

    assert results[0]["name"] == "local"
    assert results[0]["ok"] is True
    assert results[0]["command"] == "python"
