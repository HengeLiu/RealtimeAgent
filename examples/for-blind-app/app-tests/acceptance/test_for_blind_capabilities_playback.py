from __future__ import annotations

import asyncio
import ast
import json
import sys
import threading
from dataclasses import replace
from pathlib import Path

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent_python_glass.playback import PythonPlaybackEndpoint


ROOT = Path(__file__).resolve().parents[4]
APP_ROOT = ROOT / "examples" / "for-blind-app" / "audio-server"


def _clear_capability_modules() -> None:
    """清理测试进程中可能来自其他示例的 capabilities 模块。"""

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)


def _build_app(tmp_path, monkeypatch) -> RealtimeAgentApp:
    """创建 for-blind-app 测试实例。

    测试目标：复用真实样板 YAML 和自动发现配置创建应用。
    测试方法：把 app-root 加入 sys.path，并覆盖 runs / asset 路径到临时目录。
    预期结果：Tool、Task 和 MCP mock 都由公开配置装配。
    """

    _clear_capability_modules()
    monkeypatch.syspath_prepend(str(APP_ROOT))
    config = RealtimeAgentConfig.from_yaml(APP_ROOT / "server.yaml")
    return RealtimeAgentApp(
        replace(
            config,
            runs_root=str(tmp_path / "runs"),
            asset_root=str(tmp_path / "runs" / "assets"),
            memory_path=str(tmp_path / "runs"),
            tasks_store_root=str(tmp_path / "runs" / "tasks"),
            tts_provider="mock",
            tts_model="mock-tts",
            tts_voice="mock",
        )
    )


def _register_playback(app: RealtimeAgentApp) -> PythonPlaybackEndpoint:
    endpoint = PythonPlaybackEndpoint(
        app=app,
        user_id="user-for-blind",
        device_id="dev-for-blind-playback",
        sensor_profiles={
            "sensor.rgb": {
                "payloads": [
                    "hex:ffd866696e642d6f626a6563742d6672616d65ffd9",
                    "hex:ffd8747261666669632d6c696768742d6672616d65ffd9",
                    "hex:ffd86e617669676174696f6e2d6672616d65ffd9",
                ]
            }
        },
    )
    endpoint.register()
    return endpoint


def test_for_blind_capability_packages_are_auto_discovered(tmp_path, monkeypatch) -> None:
    """测试目标：确认五类老业务能力迁移样板能被 SDK 自动发现。

    测试方法：按 for-blind app YAML 创建 `RealtimeAgentApp`，读取 Tool 和 Task 注册表。
    预期结果：App 级 Tool 和 mock 业务 Task 都可见，专用 start_* Tool 不再注册。
    """

    app = _build_app(tmp_path, monkeypatch)

    assert {
        "capture_photo",
        "query_route_plan",
        "search_web",
        "task_runtime_manager",
        "start_find_object_task",
        "start_traffic_light_task",
        "start_timer_task",
    } <= set(app.tool_registry.list_names())
    assert {
        "find_object_task",
        "traffic_light_task",
        "timer_task",
    } <= set(app.task_engine.registry.list_task_types())
    assert app.discovery_errors == []


def test_for_blind_capability_paths_write_explainable_playback_artifacts(tmp_path, monkeypatch) -> None:
    """测试目标：验证 for-blind 能力成功路径和缺端失败路径都可解释。

    测试方法：注册一台具备 RGB 和 speaker 能力的 playback 设备，调用应用 Tool，
    并启动找物、红绿灯、计时器 Task。
    预期结果：单设备可完成抓拍、路线、搜索和计时器；找物 / 红绿灯因缺少
    phone/glass peer video 端明确失败，result、tool、task、asset、output 等产物能解释全链路。
    """

    app = _build_app(tmp_path, monkeypatch)
    endpoint = _register_playback(app)
    session_id = app.active_session_id("user-for-blind")

    capture = asyncio.run(
        app.tool_gateway.call(
            name="capture_photo",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={},
        )
    )
    find_object = asyncio.run(
        app.tool_gateway.call(
            name="start_find_object_task",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"object_name": "水杯"},
        )
    )
    traffic_light = asyncio.run(
        app.tool_gateway.call(
            name="start_traffic_light_task",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={},
        )
    )
    route = asyncio.run(
        app.tool_gateway.call(
            name="query_route_plan",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"origin": "家", "destination": "地铁站"},
        )
    )
    search = asyncio.run(
        app.tool_gateway.call(
            name="search_web",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"query": "盲人导航"},
        )
    )
    timer = asyncio.run(
        app.tool_gateway.call(
            name="start_timer_task",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"seconds": 0, "auto_fire": True},
        )
    )
    result = {
        "ok": all(item.ok for item in [capture, route, search, timer]) and not find_object.ok and not traffic_light.ok,
        "passed": all(item.ok for item in [capture, route, search, timer]) and not find_object.ok and not traffic_light.ok,
        "status": "ok",
        "asset_count": len(app.asset_service.query_assets(user_id="user-for-blind", stream_type="sensor.rgb")),
        "artifacts": {"result.json": str(app.recorder.session_dir(session_id, user_id="user-for-blind") / "result.json")},
    }
    app.recorder.write_result(session_id, result)

    assert result["passed"] is True
    assert result["asset_count"] >= 1

    session_dir = Path(result["artifacts"]["result.json"]).parent
    tool_events = (session_dir / "tool-events.jsonl").read_text(encoding="utf-8")
    task_signals = (session_dir / "task-signals.jsonl").read_text(encoding="utf-8")
    assets = (session_dir / "assets.jsonl").read_text(encoding="utf-8")
    output = (session_dir / "output-decisions.jsonl").read_text(encoding="utf-8")
    final_result = json.loads((session_dir / "result.json").read_text(encoding="utf-8"))

    for expected in [
        "capture_photo",
        "start_find_object_task",
        "start_traffic_light_task",
        "start_timer_task",
        "query_route_plan",
        "search_web",
    ]:
        assert expected in tool_events
    for expected in [
        "find_object.found",
        "traffic_light.state_detected",
        "timer.scheduled",
        "timer.due",
    ]:
        if expected in {"find_object.found", "traffic_light.state_detected"}:
            continue
        assert expected in task_signals
    assert "task.failed" in task_signals
    assert "找水杯的任务没有启动成功" in tool_events
    assert "红绿灯识别任务没有启动成功" in tool_events
    assert "asset.stored" in assets
    assert "play_now" in output
    assert final_result["ok"] is True


def test_timer_template_supports_create_query_cancel_and_due_notification(tmp_path, monkeypatch) -> None:
    """测试目标：覆盖计时器创建、查询、取消和到点通知四种迁移语义。

    测试方法：通过 `start_timer_task` 创建一个不自动到点的任务，随后查询并取消；
    再创建一个自动到点任务验证 `TaskContext.schedule_signal()` 会回流 `timer.due`。
    预期结果：取消任务进入 cancelled，到点任务进入 completed，并产生可播报输出。
    """

    app = _build_app(tmp_path, monkeypatch)
    endpoint = _register_playback(app)
    session_id = app.active_session_id("user-for-blind")

    created = asyncio.run(
        app.tool_gateway.call(
            name="start_timer_task",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"seconds": 30, "auto_fire": False},
        )
    )
    task_id = created.data["task_id"]
    queried = asyncio.run(
        app.tool_gateway.call(
            name="task_runtime_manager",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"action": "query", "task_id": task_id},
        )
    )
    cancelled = asyncio.run(
        app.tool_gateway.call(
            name="task_runtime_manager",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"action": "cancel", "task_id": task_id},
        )
    )
    due = asyncio.run(
        app.tool_gateway.call(
            name="start_timer_task",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"seconds": 0, "auto_fire": True},
        )
    )

    assert created.ok is True
    assert queried.data["state"] == "started"
    assert cancelled.data["state"] == "cancelled"
    assert due.data["state"] == "finished"
    session_dir = app.recorder.session_dir(session_id, user_id="user-for-blind")
    assert "play_now" in (session_dir / "output-decisions.jsonl").read_text(encoding="utf-8")


def test_timer_create_uses_real_delay_without_blocking_tool_result(tmp_path, monkeypatch) -> None:
    """测试目标：计时器创建应立即返回 running，并把到点事件按秒数延后触发。

    测试方法：替换 `threading.Timer` 为记录型 fake，不主动触发回调，通过专用 Task 启动 Tool 启动。
    预期结果：创建 60 秒计时器不会立刻 completed，且调度延迟为 60 秒。
    """

    scheduled: list[dict] = []

    class FakeTimer:
        def __init__(self, interval, function) -> None:
            self.interval = interval
            self.function = function
            self.daemon = False

        def start(self) -> None:
            scheduled.append({"interval": self.interval, "function": self.function, "daemon": self.daemon})

    monkeypatch.setattr(threading, "Timer", FakeTimer)
    app = _build_app(tmp_path, monkeypatch)
    endpoint = _register_playback(app)
    session_id = app.active_session_id("user-for-blind")

    created = asyncio.run(
        app.tool_gateway.call(
            name="start_timer_task",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"seconds": 60, "auto_fire": True},
        )
    )

    assert created.ok is True
    assert created.data["state"] == "started"
    assert scheduled and scheduled[0]["interval"] == 60
    assert endpoint.output_chunks == []


def test_timer_start_tool_creates_timer_task(tmp_path, monkeypatch) -> None:
    """测试目标：验证模型通过专用 Task 启动 Tool 能创建计时器后台任务。

    测试方法：替换 `threading.Timer` 避免真实等待，通过 `start_timer_task` 传入计时参数。
    预期结果：工具调用成功，返回 running 任务，并按秒数调度到点事件。
    """

    scheduled: list[dict] = []

    class FakeTimer:
        def __init__(self, interval, function) -> None:
            self.interval = interval
            self.function = function
            self.daemon = False

        def start(self) -> None:
            scheduled.append({"interval": self.interval, "function": self.function, "daemon": self.daemon})

    monkeypatch.setattr(threading, "Timer", FakeTimer)
    app = _build_app(tmp_path, monkeypatch)
    _register_playback(app)
    session_id = app.active_session_id("user-for-blind")

    created = asyncio.run(
        app.tool_gateway.call(
            name="start_timer_task",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"seconds": 60, "auto_fire": True},
        )
    )

    assert created.ok is True
    assert created.data["state"] == "started"
    assert scheduled and scheduled[0]["interval"] == 60


def test_for_blind_examples_use_public_api_and_no_hidden_device_routes() -> None:
    """测试目标：冻结 for-blind 样板业务代码只能依赖公开 API。

    测试方法：AST 和文本扫描 `examples/for-blind-app` 下 Python 文件。
    预期结果：不直接 import SDK 内部 service，不硬编码点对点设备路由，不携带媒体大字节字段。
    """

    forbidden_modules = (
        "realtime_agent.tools",
        "realtime_agent.tasks",
        "realtime_agent.control",
        "realtime_agent.stream",
        "realtime_agent.asset",
        "realtime_agent.output",
        "realtime_agent.protocol",
    )
    forbidden_terms = [
        "target_device",
        "target_device_id",
        "source_device_id",
        "send_to_device",
        "send_device",
        "ControlService",
        "StreamService",
        "AssetService",
        "OutputService",
        "requests.",
        "httpx.",
        "audio_base64",
        "image_base64",
        "video_base64",
    ]
    offenders: list[str] = []
    for path in sorted(APP_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            module = ""
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(forbidden_modules):
                        offenders.append(f"{path.relative_to(ROOT)}:{alias.name}")
                continue
            if module.startswith(forbidden_modules):
                offenders.append(f"{path.relative_to(ROOT)}:{module}")
        text = path.read_text(encoding="utf-8")
        for term in forbidden_terms:
            if term in text:
                offenders.append(f"{path.relative_to(ROOT)}:{term}")

    assert offenders == []
