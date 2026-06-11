from __future__ import annotations

import asyncio
import json
import time

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig


def test_builtin_tools_are_visible_and_timer_task_is_registered(tmp_path) -> None:
    """测试目标：验证基础 Tool 和计时器 Task 作为 Server SDK 内置能力注册。

    测试方法：创建默认 RealtimeAgentApp，读取 provider schema 和 Task 类型列表。
    预期结果：模型可见搜索、路线、记忆、历史、设备状态和计时器启动工具。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    tool_names = {tool["function"]["name"] for tool in app.tool_gateway.provider_schemas()}
    assert {
        "search_web",
        "query_device_state",
        "query_route_plan",
        "query_system_time",
        "memory_search",
        "manage_memory",
        "search_conversation_history",
        "start_timer_task",
        "task_runtime_manager",
    }.issubset(tool_names)
    assert [item["task_type"] for item in app.task_engine.list_task_types()] == ["timer_task"]


def test_search_web_calls_bocha_api_and_normalizes_items(tmp_path, monkeypatch) -> None:
    """测试目标：验证 SDK 内置联网搜索 Tool 调用 Bocha 并归一化结果。

    测试方法：替换 HTTP 调用，检查请求 URL、鉴权头、JSON body 和 ToolResult。
    预期结果：返回 provider=bocha，items 包含标题、URL、摘要等轻量字段。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
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
    monkeypatch.setattr("realtime_agent.tools.urllib_request.urlopen", fake_urlopen)

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


def test_query_system_time_defaults_to_beijing_and_accepts_timezone(tmp_path) -> None:
    """测试目标：验证系统时间 Tool 默认返回北京时间，并支持传入 UTC 偏移时区。

    测试方法：通过 ToolGateway 分别调用空参数和 timezone=UTC-05:30。
    预期结果：默认结果使用 Asia/Shanghai 和 +08:00；指定偏移结果使用 UTC-05:30。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    default_result = asyncio.run(
        app.tool_gateway.call(
            name="query_system_time",
            user_id="user-time",
            session_id="session-time",
            input_data={},
        )
    )
    offset_result = asyncio.run(
        app.tool_gateway.call(
            name="query_system_time",
            user_id="user-time",
            session_id="session-time",
            input_data={"timezone": "UTC-05:30"},
        )
    )

    assert default_result.ok is True
    assert default_result.data["timezone"] == "Asia/Shanghai"
    assert default_result.data["utc_offset"] == "+08:00"
    assert "T" in default_result.data["iso_datetime"]
    assert offset_result.ok is True
    assert offset_result.data["timezone"] == "UTC-05:30"
    assert offset_result.data["utc_offset"] == "-05:30"


def test_query_system_time_rejects_invalid_timezone(tmp_path) -> None:
    """测试目标：验证系统时间 Tool 对无法识别的时区返回结构化错误。

    测试方法：通过 ToolGateway 传入不存在的时区名称。
    预期结果：ToolResult 为失败状态，错误码是 invalid_argument。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    result = asyncio.run(
        app.tool_gateway.call(
            name="query_system_time",
            user_id="user-time",
            session_id="session-time",
            input_data={"timezone": "Mars/Base"},
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error["code"] == "invalid_argument"


def test_query_route_plan_uses_amap_mcp_environment(tmp_path, monkeypatch) -> None:
    """测试目标：验证路线规划 Tool 可直接使用 AMAP_MCP 环境变量调用 MCP。

    测试方法：不启用应用级 mcp 配置，替换 MCP HTTP 调用并设置 AMAP_MCP_URL、
    AMAP_MCP_BEARER_TOKEN、AMAP_MCP_API_KEY。
    预期结果：Tool 调用 maps_geo 和 maps_direction_walking，返回 provider=amap_mcp。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), mcp_enabled=False))
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
        tool_name = payload["params"]["name"]
        if tool_name == "maps_geo":
            return FakeResponse(
                {
                    "jsonrpc": "2.0",
                    "id": payload["id"],
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"return": [{"location": "121.413866,31.156773"}]}, ensure_ascii=False),
                            }
                        ]
                    },
                }
            )
        return FakeResponse({"jsonrpc": "2.0", "id": payload["id"], "result": {"distance": 120, "duration": 90}})

    monkeypatch.setenv("AMAP_MCP_URL", "https://mcp.example.com/mcp")
    monkeypatch.setenv("AMAP_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("AMAP_MCP_API_KEY", "test-key")
    monkeypatch.setattr("realtime_agent.mcp.urllib_request.urlopen", fake_urlopen)

    result = asyncio.run(
        app.tool_gateway.call(
            name="query_route_plan",
            user_id="user-route",
            session_id="session-route",
            input_data={"origin": "家", "destination": "地铁站"},
        )
    )

    assert result.ok is True
    assert result.data["provider"] == "amap_mcp"
    assert result.data["route_ready"] is True
    assert result.data["origin_location"] == "121.413866,31.156773"
    assert result.data["destination_location"] == "121.413866,31.156773"
    assert result.data["route"]["result"]["distance"] == 120
    assert calls[0]["headers"]["Authorization"] == "Bearer test-token"
    assert calls[0]["headers"]["X-api-key"] == "test-key"
    assert [call["payload"].get("params", {}).get("name") for call in calls if call["payload"].get("method") == "tools/call"] == [
        "maps_geo",
        "maps_geo",
        "maps_direction_walking",
    ]


def test_query_route_plan_requires_explicit_origin_for_current_location(tmp_path) -> None:
    """测试目标：验证路线规划 Tool 不会把“当前位置”错误地当作地址解析。

    测试方法：不传 origin，通过 ToolGateway 直接调用路线规划。
    预期结果：Tool 立即返回 route_ready=False 和 needs_origin=True，不访问外部 MCP。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    result = asyncio.run(
        app.tool_gateway.call(
            name="query_route_plan",
            user_id="user-route",
            session_id="session-route",
            input_data={"destination": "虹漕路地铁站"},
        )
    )

    assert result.ok is True
    assert result.data["route_ready"] is False
    assert result.data["provider"] == "needs_origin"
    assert result.data["needs_origin"] is True
    assert "出发地点" in result.message


def test_query_route_plan_marks_mcp_business_error_not_ready(tmp_path, monkeypatch) -> None:
    """测试目标：验证 AMap MCP 返回业务错误时路线不会被标记为已准备。

    测试方法：替换 AMap MCP Gateway，让 geocode 成功但 route_plan 返回 isError。
    预期结果：ToolResult 仍可回到模型，但 data.route_ready=False，error 包含 MCP 错误文本。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    class FakeMcp:
        def call(self, tool_name, arguments, timeout_seconds=None):
            if tool_name == "amap.geo":
                return {
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps({"return": [{"location": "121.410553,31.164033"}]}, ensure_ascii=False),
                            }
                        ]
                    }
                }
            return {"result": {"isError": True, "content": [{"type": "text", "text": "Direction Walking failed: OVER_DIRECTION_RANGE"}]}}

    monkeypatch.setattr("realtime_agent.tools._resolve_amap_mcp_gateway", lambda configured_gateway: FakeMcp())

    result = asyncio.run(
        app.tool_gateway.call(
            name="query_route_plan",
            user_id="user-route",
            session_id="session-route",
            input_data={"origin": "青岛市平度市", "destination": "虹漕路地铁站"},
        )
    )

    assert result.ok is True
    assert result.data["route_ready"] is False
    assert result.data["provider"] == "amap_mcp"
    assert "OVER_DIRECTION_RANGE" in result.data["error"]
    assert "失败" in result.message


def test_search_conversation_history_reads_runs_messages(tmp_path) -> None:
    """测试目标：验证历史对话检索 Tool 只读查询 runs 中的 messages.jsonl。

    测试方法：手工写入当前用户两个历史消息，再通过 ToolGateway 按关键词检索。
    预期结果：只返回当前用户匹配片段，并包含来源 session_id 和行号。
    """

    runs_root = tmp_path / "runs"
    messages_path = runs_root / "user-history" / "session-a" / "messages.jsonl"
    messages_path.parent.mkdir(parents=True)
    messages_path.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "我喜欢走虹漕路地铁站。"}, ensure_ascii=False),
                json.dumps({"role": "assistant", "content": "我记下你的出行偏好。"}, ensure_ascii=False),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(runs_root)))

    result = asyncio.run(
        app.tool_gateway.call(
            name="search_conversation_history",
            user_id="user-history",
            session_id="session-current",
            input_data={"query": "虹漕路", "limit": 3},
        )
    )

    assert result.ok is True
    assert result.data["count"] == 1
    assert result.data["matches"][0]["session_id"] == "session-a"
    assert result.data["matches"][0]["line"] == 1
    assert "虹漕路" in result.data["matches"][0]["text"]


def test_start_timer_task_schedules_and_finishes_immediate_timer(tmp_path) -> None:
    """测试目标：验证内置计时器 Task 可由模型可见 start_timer_task 创建并到点完成。

    测试方法：通过 ToolGateway 调用 start_timer_task，seconds=0 触发立即到点。
    预期结果：TaskRef 进入 finished，任务信号中包含 timer.scheduled 和 timer.due。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    result = asyncio.run(
        app.tool_gateway.call(
            name="start_timer_task",
            user_id="user-timer",
            session_id="session-timer",
            input_data={"seconds": 0, "message": "时间到了"},
        )
    )

    assert result.ok is True
    task_id = result.data["task_id"]
    ref = app.task_engine.query(task_id)
    assert ref.state == "finished"
    signals = [signal.signal_name for signal in app.task_engine.store.signals_for_task(task_id)]
    assert "timer.scheduled" in signals
    assert "timer.due" in signals
    assert "task.finished" in signals


def test_start_timer_task_returns_before_timer_due(tmp_path) -> None:
    """测试目标：验证 Task 启动工具只返回后台任务引用，不等待计时器最终到点。

    测试方法：通过 ToolGateway 创建 1 秒计时器，立即查询任务状态和调度信号。
    预期结果：调用返回时任务仍处于 started，已经存在待触发 schedule；等待后才进入 finished。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    result = asyncio.run(
        app.tool_gateway.call(
            name="start_timer_task",
            user_id="user-timer-async",
            session_id="session-timer-async",
            input_data={"seconds": 1, "message": "一秒到了"},
        )
    )

    assert result.ok is True
    task_id = result.data["task_id"]
    assert app.task_engine.query(task_id).state == "started"
    schedules = app.task_engine.list_scheduled_signals()
    assert [item["task_id"] for item in schedules] == [task_id]
    assert schedules[0]["signal_name"] == "timer.due"

    time_limit = time.monotonic() + 2.0
    while app.task_engine.query(task_id).state == "started":
        if time.monotonic() > time_limit:
            break
        time.sleep(0.05)

    assert app.task_engine.query(task_id).state == "finished"
