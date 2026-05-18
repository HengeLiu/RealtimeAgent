from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

import yaml

from audio_chat.app import AudioChatApp, AudioChatConfig


ROOT = Path(__file__).resolve().parents[3]
APP_ROOT = ROOT / "examples" / "for-blind-app" / "audio-server"


def _clear_capability_modules() -> None:
    """清理测试进程中已经导入的 capabilities 模块，避免不同 app-root 串包。"""

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)


def _build_app(tmp_path, monkeypatch, **overrides) -> AudioChatApp:
    """构造 for-blind-app 测试实例。

    测试目标：让导航和搜索 Tool 通过真实自动发现路径注册。
    测试方法：加载 app-root 的 server.yaml，仅覆盖运行产物目录和测试专用配置。
    预期结果：ToolGateway 调用时会经过 Pydantic 入参默认值和真实 ToolContext 注入。
    """

    _clear_capability_modules()
    monkeypatch.syspath_prepend(str(APP_ROOT))
    config = AudioChatConfig.from_yaml(APP_ROOT / "server.yaml")
    return AudioChatApp(
        replace(
            config,
            runs_root=str(tmp_path / "runs"),
            asset_root=str(tmp_path / "runs" / "assets"),
            memory_path=str(tmp_path / "runs"),
            **overrides,
        )
    )


def test_query_route_plan_calls_configured_amap_mcp(tmp_path, monkeypatch) -> None:
    """测试目标：验证导航 Tool 会调用配置好的 Amap MCP，而不是永远 fallback。

    测试方法：写入包含 `amap.route_plan` 的 MCP mock 配置，启用 MCP 后调用
    `query_route_plan`。
    预期结果：ToolResult 成功，provider 为 `amap_mcp`，且结果包含 MCP 返回的路线。
    """

    config_path = tmp_path / "mcp.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "tools": [
                    {
                        "name": "amap.route_plan",
                        "description": "高德路线规划",
                        "mock_result": {"distance": 120, "duration": 90},
                    }
                ]
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    app = _build_app(tmp_path, monkeypatch, mcp_enabled=True, mcp_config_path=str(config_path))

    result = asyncio.run(
        app.tool_gateway.call(
            name="query_route_plan",
            user_id="user-route",
            session_id="session-route",
            input_data={"origin": "家", "destination": "地铁站"},
        )
    )

    assert result.ok is True
    assert result.data["route_ready"] is True
    assert result.data["provider"] == "amap_mcp"
    assert result.data["route"]["result"]["distance"] == 120


def test_search_web_calls_bocha_api_and_normalizes_items(tmp_path, monkeypatch) -> None:
    """测试目标：验证联网搜索 Tool 调用 Bocha Web Search API 并归一化结果。

    测试方法：通过 monkeypatch 替换 HTTP 调用，检查请求 URL、鉴权头和 JSON body。
    预期结果：ToolResult 返回 provider=bocha，items 中包含标题、URL 和摘要字段。
    """

    app = _build_app(tmp_path, monkeypatch)
    from capabilities import tools as app_tools

    captured = {}

    class FakeResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self) -> bytes:
            return json.dumps(
                {
                    "data": {
                        "webPages": {
                            "value": [
                                {
                                    "name": "盲人导航安全指南",
                                    "url": "https://example.com/nav",
                                    "snippet": "出行前确认路线。",
                                    "summary": "过路口时应确认红绿灯和车辆状态。",
                                    "siteName": "Example",
                                    "datePublished": "2026-05-18",
                                }
                            ]
                        }
                    }
                },
                ensure_ascii=False,
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("BOCHA_SEARCH_API_KEY", "test-bocha-key")
    monkeypatch.setenv("BOCHA_SEARCH_API_URL", "https://api.bochaai.com/v1/web-search")
    monkeypatch.setattr(app_tools.urllib_request, "urlopen", fake_urlopen)

    result = asyncio.run(
        app.tool_gateway.call(
            name="search_web",
            user_id="user-search",
            session_id="session-search",
            input_data={"query": "盲人导航", "limit": 1, "freshness": "oneWeek"},
        )
    )

    assert result.ok is True
    assert result.data["provider"] == "bocha"
    assert result.data["fallback"] is False
    assert result.data["items"][0]["title"] == "盲人导航安全指南"
    assert result.data["items"][0]["url"] == "https://example.com/nav"
    assert captured["url"] == "https://api.bochaai.com/v1/web-search"
    assert captured["headers"]["Authorization"] == "Bearer test-bocha-key"
    assert captured["body"]["query"] == "盲人导航"
    assert captured["body"]["count"] == 1
    assert captured["body"]["freshness"] == "oneWeek"
