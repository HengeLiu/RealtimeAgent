import json
from pathlib import Path

from audio_chat.observability import RunRecorder
from audio_chat.protocol import StreamChunk


def test_runs_layout_uses_user_device_directory(tmp_path: Path) -> None:
    """测试目标：验证 runs 目录只使用 user_id/device_id 组织对话产物。

    测试方法：绑定用户和设备，写入消息、模型请求和音频 stream payload。
    预期结果：`messages.jsonl`、`model-request.json` 和音频文件都位于
    `runs/<user_id>/<device_id>/` 下，不再创建 `sessions` 或 `users` 目录。
    """

    recorder = RunRecorder(tmp_path / "runs")
    recorder.bind_device(user_id="user-layout", device_id="dev-layout")
    recorder.record_message("user-layout", {"role": "user", "content": "hello", "session_id": "dev-layout"})
    recorder.record_model_request("dev-layout", {"user_id": "user-layout", "model": "mock", "messages": []})
    recorder.record_stream_payload(
        StreamChunk(
            user_id="user-layout",
            session_id="dev-layout",
            stream_id="stream-audio",
            stream_type="sensor.mic",
            seq=0,
            payload=b"\x01\x02",
            final=True,
        )
    )

    device_dir = tmp_path / "runs" / "user-layout" / "dev-layout"
    assert (device_dir / "messages.jsonl").exists()
    assert json.loads((device_dir / "model-request.json").read_text(encoding="utf-8"))["model"] == "mock"
    assert (device_dir / "audio" / "input-stream-audio.pcm").read_bytes() == b"\x01\x02"
    assert not (tmp_path / "runs" / "sessions").exists()
    assert not (tmp_path / "runs" / "users").exists()


def test_runs_layout_stores_sensor_assets_in_type_directories(tmp_path: Path) -> None:
    """测试目标：验证照片和 IMU 等传感器资产按类型进入子目录。

    测试方法：通过 RunRecorder 的媒体目录解析照片和 IMU 目录。
    预期结果：RGB 照片进入 `photos`，IMU 数据进入 `imu`。
    """

    recorder = RunRecorder(tmp_path / "runs")
    recorder.bind_device(user_id="user-layout", device_id="dev-layout")

    assert recorder.media_dir("dev-layout", "sensor.rgb") == tmp_path / "runs" / "user-layout" / "dev-layout" / "photos"
    assert recorder.media_dir("dev-layout", "sensor.imu") == tmp_path / "runs" / "user-layout" / "dev-layout" / "imu"
