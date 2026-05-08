from __future__ import annotations

import json
from pathlib import Path

from audio_chat_python_glass.playback import run_playback


def test_old_sdk_parity_playback_runs_tool_task_and_timer_scenarios(tmp_path: Path) -> None:
    """测试目标：验证设备级回放能覆盖老 SDK 常用的抓拍、连续视觉和计时器输出场景。

    测试方法：加载 basic-app 能力样板，使用 in-process playback 注册一台通用设备，
    依次调用 `capture_photo` Tool、`continuous_rgb_analyze` Task 和 `timer` Task。
    预期结果：回放通过事件和 stream 写入资产与播放产物，并生成可解释的标准文件。
    """

    audio_chat_root = Path(__file__).resolve().parents[2]
    result = run_playback(
        {
            "mode": "in_process",
            "runs_root": str(tmp_path / "runs"),
            "asset_root": str(tmp_path / "assets"),
            "app_root": str(audio_chat_root / "tests" / "fixtures" / "basic_app"),
            "user_id": "user-old-sdk-parity",
            "device_id": "dev-old-sdk-parity-playback",
            "scenario": {
                "actions": [
                    {"type": "call_tool", "name": "capture_photo", "input": {"reason": "old-sdk-parity"}},
                    {
                        "type": "start_task",
                        "task_type": "continuous_rgb_analyze",
                        "input": {"frame_limit": 2, "timeout_seconds": 1},
                    },
                    {"type": "start_task", "task_type": "timer", "input": {"seconds": 1}},
                ],
                "assert": {
                    "expected_events": [
                        "control.device.registered",
                        "stream.control.configure.requested",
                        "stream.output.open.requested",
                        "stream.output.close.requested",
                    ],
                    "expected_stream_types": ["sensor.rgb", "actuator.speaker"],
                    "expected_asset_count": 3,
                    "expected_tool_events": ["capture_photo"],
                    "expected_task_events": ["continuous_rgb_analyze.frames_collected", "timer.started"],
                    "expected_output_chunks": 1,
                },
            },
        }
    )

    assert result["passed"] is True, result["assertions"]
    assert result["asset_count"] >= 3
    assert result["tool_event_count"] > 0
    assert result["task_event_count"] > 0
    assert result["actuator_event_count"] > 0

    session_dir = tmp_path / "runs" / "sessions" / result["session_id"]
    required = [
        "events.jsonl",
        "stream-events.jsonl",
        "agent-events.jsonl",
        "tool-events.jsonl",
        "task-events.jsonl",
        "assets.jsonl",
        "output-decisions.jsonl",
        "actuators.jsonl",
        "result.json",
    ]
    assert [name for name in required if not (session_dir / name).exists()] == []

    result_json = json.loads((session_dir / "result.json").read_text(encoding="utf-8"))
    assert result_json["ok"] is True
    assert "capture_photo" in (session_dir / "tool-events.jsonl").read_text(encoding="utf-8")
    assert "continuous_rgb_analyze.frames_collected" in (session_dir / "task-events.jsonl").read_text(encoding="utf-8")
    assert "timer.started" in (session_dir / "task-events.jsonl").read_text(encoding="utf-8")


def test_old_sdk_parity_playback_supports_depth_imu_heading_and_location(tmp_path: Path) -> None:
    """测试目标：验证回放配置能表达 depth、IMU、heading 和 location 传感器时间线。

    测试方法：通过 configure_stream action 请求 `sensor.depth` 和 `sensor.imu` 连续上传，
    并在 playback 配置中写入 heading/location 元数据。
    预期结果：资产产物包含 depth/IMU stream，IMU 资产元数据保留 heading/location。
    """

    result = run_playback(
        {
            "mode": "in_process",
            "runs_root": str(tmp_path / "runs"),
            "asset_root": str(tmp_path / "assets"),
            "user_id": "user-sensor-parity",
            "device_id": "dev-sensor-parity",
            "heading": {"degrees": 92.5, "accuracy": "mock"},
            "location": {"latitude": 31.2304, "longitude": 121.4737, "accuracy_m": 5},
            "scenario": {
                "actions": [
                    {
                        "type": "configure_stream",
                        "stream_type": "sensor.depth",
                        "payload": {"mode": "continuous", "max_samples": 2, "correlation_id": "depth-line"},
                    },
                    {
                        "type": "configure_stream",
                        "stream_type": "sensor.imu",
                        "payload": {"mode": "continuous", "max_samples": 3, "correlation_id": "imu-line"},
                    },
                ],
                "assert": {
                    "expected_events": ["control.device.registered", "stream.control.configure.requested"],
                    "expected_stream_types": ["sensor.depth", "sensor.imu"],
                    "expected_asset_count": 5,
                    "expected_output_chunks": 0,
                },
            },
        }
    )

    assert result["passed"] is True, result["assertions"]
    session_dir = tmp_path / "runs" / "sessions" / result["session_id"]
    assets_text = (session_dir / "assets.jsonl").read_text(encoding="utf-8")
    assert "sensor.depth" in assets_text
    assert "sensor.imu" in assets_text
    assert "heading" in assets_text
    assert "location" in assets_text
