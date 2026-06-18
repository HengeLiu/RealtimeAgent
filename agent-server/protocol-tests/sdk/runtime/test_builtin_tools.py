from __future__ import annotations

import asyncio
import json
import time

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.errors import ErrorCode
from realtime_agent.protocol import Event
from realtime_agent.tools import BaseTool, ToolError, ToolRegistry, ToolResult, ToolSpec


class RecordingEndpoint:
    """记录测试端侧收到的控制事件。

    主要功能：模拟一台可以注册到 ControlService 的端侧设备。
    主要属性：`events` 保存 server 下发到端侧的控制事件。
    """

    def __init__(self, *, user_id: str, device_id: str) -> None:
        self.user_id = user_id
        self.device_id = device_id
        self.events: list[Event] = []

    def push_event(self, event: Event) -> None:
        """记录 server 下发事件。"""

        self.events.append(event)


def register_endpoint(app: RealtimeAgentApp, endpoint: RecordingEndpoint, *, properties: dict | None = None) -> None:
    """注册一台测试端侧设备。

    主要逻辑：使用真实 `register_device()` 路径注册设备，保证 properties 会参与
    系统路由编译。
    参数：`app` 为测试应用，`endpoint` 为记录端侧，`properties` 为设备属性。
    返回值：无。
    异常情况：注册失败时断言失败。
    """

    response = app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id=endpoint.user_id,
            producer_id=endpoint.device_id,
            payload={
                "device_id": endpoint.device_id,
                "device_name": endpoint.device_id,
                "client_type": "builtin-tool-test",
                "sdk_version": "realtime-agent-test",
                "auth": {"mode": "disabled"},
                "supports": {},
                "properties": dict(properties or {}),
            },
        ),
        endpoint,
    )
    assert response.event_name == "control.device.registered"


def test_builtin_tools_are_visible_and_timer_tool_is_registered(tmp_path) -> None:
    """测试目标：验证基础 Tool 和计时器 Tool 作为 Server SDK 内置能力注册。

    测试方法：创建默认 RealtimeAgentApp，读取 provider schema 列表。
    预期结果：模型可见搜索、路线、记忆、历史、设备状态、计时器和后台运行管理工具；
    计时器是后台 Tool（start_timer），耗时能力统一由 Tool Run 机制承载。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    tool_names = {tool["function"]["name"] for tool in app.tool_gateway.provider_schemas()}
    assert {
        "search_web",
        "query_device_state",
        "query_route_plan",
        "query_system_time",
        "query_current_location",
        "memory_search",
        "manage_memory",
        "search_conversation_history",
        "start_timer",
        "tool_run_manager",
    }.issubset(tool_names)


def test_tool_gateway_uses_three_second_default_and_rejects_over_budget_tool(tmp_path) -> None:
    """测试目标：验证前台 Tool 默认超时为 3 秒，且单个 Tool 不能超过模型等待上限。

    测试方法：读取内置 Tool schema 的 timeout_seconds，再向注册表注册一个声明
    4 秒超时的测试 Tool。
    预期结果：默认 schema 为 3 秒；超出 `max_wait_timeout_seconds` 的 Tool 被拒绝。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    schemas = {schema["name"]: schema for schema in app.tool_gateway.schemas()}

    class SlowTool(BaseTool):
        """测试用慢 Tool。"""

        spec = ToolSpec(name="slow_tool", description="测试慢工具", timeout_seconds=4)

        async def run(self, context, input_data):
            return ToolResult.success(data={})

    assert schemas["query_system_time"]["timeout_seconds"] == 3
    registry = ToolRegistry(default_timeout_seconds=3, max_wait_timeout_seconds=3)
    try:
        registry.register(SlowTool())
    except ToolError as exc:
        assert exc.code == ErrorCode.PROTOCOL_ERROR
        assert exc.details["max_wait_timeout_seconds"] == 3
    else:
        raise AssertionError("超出模型等待上限的 Tool 不应注册成功")


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


def test_query_current_location_returns_message_without_capable_device(tmp_path) -> None:
    """测试目标：验证定位 Tool 在没有可消费端侧时立即返回提醒。

    测试方法：分别在无设备和设备未声明定位能力时调用 `query_current_location`。
    预期结果：Tool 不等待超时，返回 location_ready=False 和明确 provider。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    no_device = asyncio.run(
        app.tool_gateway.call(
            name="query_current_location",
            user_id="user-location",
            session_id="session-location",
            input_data={},
        )
    )
    endpoint = RecordingEndpoint(user_id="user-location", device_id="dev-location-basic")
    register_endpoint(app, endpoint, properties={"demo.name": "basic"})
    no_capability = asyncio.run(
        app.tool_gateway.call(
            name="query_current_location",
            user_id="user-location",
            session_id="session-location",
            input_data={},
        )
    )

    assert no_device.ok is True
    assert no_device.data["provider"] == "no_active_device"
    assert no_device.data["location_ready"] is False
    assert no_capability.ok is True
    assert no_capability.data["provider"] == "no_capable_device"
    assert no_capability.data["location_ready"] is False
    assert endpoint.events == []


def test_query_current_location_without_amap_returns_no_coordinates(tmp_path) -> None:
    """测试目标：验证拿到端侧坐标但无法逆地理编码时不回退裸经纬度。

    测试方法：注册带 `realtime_agent.location=true` 的设备并回发坐标，但不配置高德 MCP，
    逆地理编码会因网关不可用而失败。
    预期结果：Tool 发出定位命令并读取坐标，但因拿不到地名返回 location_ready=False、
    provider=address_unavailable，且结果里不含经纬度（坐标对用户无意义）。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-location-ok"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-location")
    register_endpoint(
        app,
        endpoint,
        properties={"realtime_agent.location": True, "realtime_agent.location_commands": ["device.location.get_current"]},
    )

    async def _run():
        task = asyncio.create_task(
            app.tool_gateway.call(
                name="query_current_location",
                user_id=user_id,
                session_id="session-location",
                input_data={"timeout_seconds": 1},
            )
        )
        deadline = asyncio.get_running_loop().time() + 1
        while not endpoint.events and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert endpoint.events
        command = endpoint.events[-1]
        app.publish_control_event(
            Event(
                event_name="command.completed",
                user_id=user_id,
                producer_id="dev-location",
                payload={
                    "command_id": command.payload["command_id"],
                    "command": "device.location.get_current",
                    "location": {"latitude": 31.164033, "longitude": 121.410553, "accuracy": 18.5},
                },
            )
        )
        return await asyncio.wait_for(task, timeout=2)

    result = asyncio.run(_run())

    assert endpoint.events[-1].payload["command"] == "device.location.get_current"
    assert result.ok is True
    assert result.data["location_ready"] is False
    assert result.data["provider"] == "address_unavailable"
    assert result.data["address"] is None
    # 关键：绝不回退裸经纬度。
    assert "latitude" not in result.data
    assert "longitude" not in result.data


def test_wgs84_to_gcj02_matches_known_shanghai_point() -> None:
    """测试目标：锁定 WGS-84→GCJ-02 转换结果，避免回归。

    测试方法：用已在高德上核对过的上海坐标做转换，并验证境外坐标按原值返回。
    预期结果：转换后坐标与高德实测一致（误差 <1e-4），境外坐标不变。
    """

    from realtime_agent.tools import _out_of_china, _wgs84_to_gcj02

    gcj_lat, gcj_lng = _wgs84_to_gcj02(31.173648, 121.405517)
    assert abs(gcj_lat - 31.171785) < 1e-4
    assert abs(gcj_lng - 121.410163) < 1e-4
    # 境外坐标 GCJ-02 与 WGS-84 一致，直接返回。
    assert _out_of_china(37.0, -122.0) is True
    assert _wgs84_to_gcj02(37.0, -122.0) == (37.0, -122.0)


def test_compose_place_from_poi_builds_precise_address() -> None:
    """测试目标：用 around_search POI 的 pname/cityname/adname/address 拼出精确地点。

    预期结果：省/市/区去重后拼接，门牌地址作为主体并加“附近”；无 address 时退回 POI 名称。
    """

    from realtime_agent.tools import _compose_place_from_poi

    place, components = _compose_place_from_poi(
        {
            "pname": "上海市",
            "cityname": "上海市",
            "adname": "徐汇区",
            "address": "虹梅路街道虹漕路88号",
            "name": "H88越虹广场",
        }
    )
    assert place == "上海市徐汇区虹梅路街道虹漕路88号附近"
    assert components == {"province": "上海市", "city": "上海市", "district": "徐汇区"}

    # 没有 address 时退回 POI 名称。
    place2, _ = _compose_place_from_poi({"pname": "上海市", "cityname": "上海市", "adname": "徐汇区", "name": "锦和中心"})
    assert place2 == "上海市徐汇区锦和中心附近"


def test_query_current_location_resolves_address_via_around_search(tmp_path, monkeypatch) -> None:
    """测试目标：定位 Tool 拿到端侧 WGS-84 后转 GCJ-02，用 around_search 解析门牌级地点。

    测试方法：注册声明定位能力的端侧并回发 WGS-84 坐标，替换 AMap MCP Gateway 让
    amap.around_search 返回最近 POI（自带 pname/cityname/adname/address）。
    预期结果：送给高德的是转换后的 GCJ-02 坐标；address 为“…虹漕路88号附近”，带行政区划和 nearby_poi。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-location-addr"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-location-addr")
    register_endpoint(
        app,
        endpoint,
        properties={"realtime_agent.location": True, "realtime_agent.location_commands": ["device.location.get_current"]},
    )

    class FakeMcp:
        def __init__(self) -> None:
            self.calls = []

        def call(self, tool_name, arguments, timeout_seconds=None):
            self.calls.append({"tool_name": tool_name, "arguments": dict(arguments)})
            poi = {
                "name": "H88越虹广场",
                "address": "虹梅路街道虹漕路88号",
                "pname": "上海市",
                "cityname": "上海市",
                "adname": "徐汇区",
            }
            return {"result": {"content": [{"type": "text", "text": json.dumps({"pois": [poi]}, ensure_ascii=False)}]}}

    fake_mcp = FakeMcp()
    monkeypatch.setattr("realtime_agent.tools._resolve_amap_mcp_gateway", lambda configured_gateway: fake_mcp)

    async def _run():
        task = asyncio.create_task(
            app.tool_gateway.call(
                name="query_current_location",
                user_id=user_id,
                session_id="session-location",
                input_data={"timeout_seconds": 1},
            )
        )
        deadline = asyncio.get_running_loop().time() + 1
        while not endpoint.events and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert endpoint.events
        command = endpoint.events[-1]
        app.publish_control_event(
            Event(
                event_name="command.completed",
                user_id=user_id,
                producer_id="dev-location-addr",
                payload={
                    "command_id": command.payload["command_id"],
                    "command": "device.location.get_current",
                    "location": {"latitude": 31.173648, "longitude": 121.405517, "accuracy": 19.5},
                },
            )
        )
        return await asyncio.wait_for(task, timeout=2)

    result = asyncio.run(_run())

    assert result.ok is True
    assert result.data["location_ready"] is True
    assert result.data["address"] == "上海市徐汇区虹梅路街道虹漕路88号附近"
    assert result.data["address_components"]["district"] == "徐汇区"
    assert result.data["nearby_poi"] == "H88越虹广场"
    assert result.data["coordinate_system"] == "wgs84"
    # 送给高德的是转换后的 GCJ-02 坐标，而非原始 WGS-84。
    around_calls = [call for call in fake_mcp.calls if call["tool_name"] == "amap.around_search"]
    assert around_calls
    sent_lng = float(around_calls[-1]["arguments"]["location"].split(",")[0])
    assert abs(sent_lng - 121.410163) < 1e-4


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


def test_query_route_plan_current_location_falls_back_to_origin_prompt(tmp_path) -> None:
    """测试目标：验证路线规划 Tool 在无法定位当前位置时提示模型追问起点。

    测试方法：不传 origin，且不注册端侧设备，通过 ToolGateway 调用路线规划。
    预期结果：Tool 不把“当前位置”当地址解析，返回 route_ready=False 和 needs_origin=True。
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


def test_query_route_plan_resolves_current_location_with_device_gps(tmp_path, monkeypatch) -> None:
    """测试目标：验证路线规划 Tool 会在 origin=当前位置时先请求端侧 GPS。

    测试方法：注册声明定位能力的端侧，异步调用路线规划后回发定位结果，并替换
    AMap MCP Gateway 记录 geocode 和 route 调用。
    预期结果：路线规划使用端侧经纬度作为 origin，且不把“当前位置”送去 geocode。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    user_id = "user-route-gps"
    endpoint = RecordingEndpoint(user_id=user_id, device_id="dev-route-gps")
    register_endpoint(
        app,
        endpoint,
        properties={"realtime_agent.location": True, "realtime_agent.location_commands": ["device.location.get_current"]},
    )

    class FakeMcp:
        def __init__(self) -> None:
            self.calls = []

        def call(self, tool_name, arguments, timeout_seconds=None):
            self.calls.append({"tool_name": tool_name, "arguments": dict(arguments), "timeout_seconds": timeout_seconds})
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
            return {"result": {"distance": 120, "duration": 90}}

    fake_mcp = FakeMcp()
    monkeypatch.setattr("realtime_agent.tools._resolve_amap_mcp_gateway", lambda configured_gateway: fake_mcp)

    async def _run():
        task = asyncio.create_task(
            app.tool_gateway.call(
                name="query_route_plan",
                user_id=user_id,
                session_id="session-route",
                input_data={"origin": "当前位置", "destination": "虹漕路地铁站", "timeout_seconds": 3},
            )
        )
        deadline = asyncio.get_running_loop().time() + 1
        while not endpoint.events and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert endpoint.events
        command = endpoint.events[-1]
        app.publish_control_event(
            Event(
                event_name="command.completed",
                user_id=user_id,
                producer_id="dev-route-gps",
                payload={
                    "command_id": command.payload["command_id"],
                    "command": "device.location.get_current",
                    "location": {"latitude": 31.230525, "longitude": 121.473667, "accuracy": 21.0},
                },
            )
        )
        return await asyncio.wait_for(task, timeout=3)

    result = asyncio.run(_run())

    assert result.ok is True
    assert result.data["route_ready"] is True
    assert result.data["origin_location"] == "121.473667,31.230525"
    assert result.data["origin_location_source"] == "device_gps"
    assert result.data["origin_location_accuracy_meters"] == 21.0
    assert [call["tool_name"] for call in fake_mcp.calls] == ["amap.geo", "amap.route_plan"]
    assert fake_mcp.calls[-1]["arguments"]["origin"] == "121.473667,31.230525"


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


def test_query_route_plan_reports_timeout_to_model(tmp_path, monkeypatch) -> None:
    """测试目标：验证路线规划 MCP 超时时 Tool 会返回可读失败原因。

    测试方法：替换 AMap MCP Gateway，让路线调用抛出 TimeoutError。
    预期结果：ToolResult 仍能回到模型，route_ready=False，provider=timeout。
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
            raise TimeoutError("simulated route timeout")

    monkeypatch.setattr("realtime_agent.tools._resolve_amap_mcp_gateway", lambda configured_gateway: FakeMcp())

    result = asyncio.run(
        app.tool_gateway.call(
            name="query_route_plan",
            user_id="user-route",
            session_id="session-route",
            input_data={"origin": "上海市", "destination": "虹漕路地铁站"},
        )
    )

    assert result.ok is True
    assert result.data["route_ready"] is False
    assert result.data["provider"] == "timeout"
    assert "timeout" in result.data["error"]
    assert "超时" in result.message


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


def test_start_timer_immediate_completes_inline(tmp_path) -> None:
    """测试目标：start_timer 工具对 0 秒计时在等待窗口内完成并返回到点文案。

    测试方法：通过 ToolGateway 调用 start_timer，seconds=0。
    预期结果：返回最终结果（status=completed），消息为到点文案，运行 completed_inline。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    result = asyncio.run(
        app.tool_gateway.call(
            name="start_timer",
            user_id="user-timer",
            session_id="session-timer",
            input_data={"seconds": 0, "message": "时间到了"},
        )
    )

    assert result.ok is True
    assert result.status == "completed"
    assert "时间到了" in result.message
    runs = [run for run in app.tool_gateway.tool_run_store.list_runs() if run.tool_name == "start_timer"]
    assert runs and runs[-1].state == "completed_inline"


def test_start_timer_returns_running_then_fires(tmp_path) -> None:
    """测试目标：start_timer 工具超窗立即返回“运行中”，到点后后台完成。

    测试方法：把等待窗口压短，启动 1 秒计时器；立即查看返回为 running，等待后台到点。
    预期结果：调用立即返回 running，运行进入 reported_running；等待后到达终态。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    app.tool_gateway.executor.wait_window_seconds = 0.1

    result = asyncio.run(
        app.tool_gateway.call(
            name="start_timer",
            user_id="user-timer-async",
            session_id="session-timer-async",
            input_data={"seconds": 1, "message": "一秒到了"},
        )
    )

    assert result.ok is True
    assert result.status == "running"
    run_id = result.data["tool_run_id"]
    assert app.tool_gateway.tool_run_store.get(run_id).state == "reported_running"

    time_limit = time.monotonic() + 3.0
    while not app.tool_gateway.tool_run_store.get(run_id).is_terminal:
        if time.monotonic() > time_limit:
            break
        time.sleep(0.05)
    # 到点后会话未开启，late result 走待通知，运行进入 followed_up 终态。
    assert app.tool_gateway.tool_run_store.get(run_id).state == "followed_up"
