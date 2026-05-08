from __future__ import annotations

import asyncio
import ast
import json
import sys
from dataclasses import replace
from pathlib import Path

import yaml

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat_python_glass.playback import PythonPlaybackEndpoint


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app-examples" / "for-blind-app"


def _clear_capability_modules() -> None:
    """清理测试进程中可能来自其他示例的 capabilities 模块。"""

    for name in list(sys.modules):
        if name == "capabilities" or name.startswith("capabilities."):
            sys.modules.pop(name, None)


def _build_app(tmp_path, monkeypatch) -> AudioChatApp:
    """创建 for-blind-app 测试实例。

    测试目标：复用真实样板 YAML 和自动发现配置创建应用。
    测试方法：把 app-root 加入 sys.path，并覆盖 runs / asset 路径到临时目录。
    预期结果：Tool、Task 和 MCP mock 都由公开配置装配。
    """

    _clear_capability_modules()
    monkeypatch.syspath_prepend(str(APP_ROOT))
    config = AudioChatConfig.from_yaml(APP_ROOT / "server.yaml")
    return AudioChatApp(
        replace(
            config,
            runs_root=str(tmp_path / "runs"),
            asset_root=str(tmp_path / "runs" / "assets"),
            memory_path=str(tmp_path / "runs" / "memory"),
        )
    )


def _register_playback(app: AudioChatApp) -> PythonPlaybackEndpoint:
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

    测试方法：按 for-blind app YAML 创建 `AudioChatApp`，读取 Tool 和 Task 注册表。
    预期结果：find_object、traffic_light、navigation、search、timer 的 Tool / Task 都可见。
    """

    app = _build_app(tmp_path, monkeypatch)

    assert {
        "find_object_capture",
        "start_find_object",
        "start_traffic_light",
        "prepare_navigation",
        "start_navigation",
        "search_web",
        "timer",
    } <= set(app.tool_registry.list_names())
    assert {
        "find_object_vision_task",
        "traffic_light_task",
        "navigation_task",
        "timer_task",
    } <= set(app.task_engine.registry.list_task_types())
    assert app.discovery_errors == []


def test_for_blind_five_capability_success_paths_write_explainable_playback_artifacts(tmp_path, monkeypatch) -> None:
    """测试目标：验证五类老业务样板至少各有一个成功路径回放。

    测试方法：注册具备 RGB 和 speaker 能力的 playback 设备，依次调用找物、红绿灯、
    导航、搜索和计时器 Tool，Task 通过 event + stream 获取视觉资产并写出运行产物。
    预期结果：result、tool、task、asset、output 等产物能解释全链路。
    """

    app = _build_app(tmp_path, monkeypatch)
    endpoint = _register_playback(app)
    scenario = yaml.safe_load((APP_ROOT / "host/glass-playback/old-sdk-parity-capabilities.yaml").read_text(encoding="utf-8"))[
        "scenario"
    ]

    result = endpoint.run_scripted(scenario)

    assert result["passed"] is True
    assert result["asset_count"] >= 6
    assert result["output_chunk_count"] >= 5
    assert "sensor.rgb" in result["stream_types"]
    assert "actuator.speaker" in result["stream_types"]

    session_dir = Path(result["artifacts"]["result.json"]).parent
    tool_events = (session_dir / "tool-events.jsonl").read_text(encoding="utf-8")
    task_events = (session_dir / "task-events.jsonl").read_text(encoding="utf-8")
    assets = (session_dir / "assets.jsonl").read_text(encoding="utf-8")
    output = (session_dir / "output-decisions.jsonl").read_text(encoding="utf-8")
    final_result = json.loads((session_dir / "result.json").read_text(encoding="utf-8"))

    for expected in [
        "find_object_capture",
        "start_find_object",
        "start_traffic_light",
        "prepare_navigation",
        "start_navigation",
        "search_web",
        "timer",
    ]:
        assert expected in tool_events
    for expected in [
        "find_object.found",
        "traffic_light.state_detected",
        "navigation.deviation_detected",
        "navigation.near_destination",
        "navigation.visual_confirmed",
        "timer.scheduled",
        "timer.due",
    ]:
        assert expected in task_events
    assert "asset.stored" in assets
    assert "play_now" in output
    assert final_result["ok"] is True


def test_timer_template_supports_create_query_cancel_and_due_notification(tmp_path, monkeypatch) -> None:
    """测试目标：覆盖计时器创建、查询、取消和到点通知四种迁移语义。

    测试方法：通过 `timer` Tool 创建一个不自动到点的任务，随后查询并取消；再创建
    一个自动到点任务验证 `TaskContext.schedule_event()` 会回流 `timer.due`。
    预期结果：取消任务进入 cancelled，到点任务进入 completed，并产生可播报输出。
    """

    app = _build_app(tmp_path, monkeypatch)
    endpoint = _register_playback(app)
    session_id = app.active_session_id("user-for-blind")

    created = asyncio.run(
        app.tool_gateway.call(
            name="timer",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"action": "create", "seconds": 30, "auto_fire": False},
        )
    )
    task_id = created.data["task_id"]
    queried = asyncio.run(
        app.tool_gateway.call(
            name="timer",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"action": "query", "task_id": task_id},
        )
    )
    cancelled = asyncio.run(
        app.tool_gateway.call(
            name="timer",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"action": "cancel", "task_id": task_id},
        )
    )
    due = asyncio.run(
        app.tool_gateway.call(
            name="timer",
            user_id="user-for-blind",
            session_id=session_id,
            input_data={"action": "create", "seconds": 0, "auto_fire": True},
        )
    )

    assert created.ok is True
    assert queried.data["state"] == "running"
    assert cancelled.data["state"] == "cancelled"
    assert due.data["state"] == "completed"
    assert endpoint.output_chunks


def test_for_blind_examples_use_public_api_and_no_hidden_device_routes() -> None:
    """测试目标：冻结 for-blind 样板业务代码只能依赖公开 API。

    测试方法：AST 和文本扫描 `examples/for-blind-app` 下 Python 文件。
    预期结果：不直接 import SDK 内部 service，不硬编码点对点设备路由，不携带媒体大字节字段。
    """

    forbidden_modules = (
        "audio_chat.tools",
        "audio_chat.tasks",
        "audio_chat.control",
        "audio_chat.stream",
        "audio_chat.asset",
        "audio_chat.output",
        "audio_chat.protocol",
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
