"""眼镜端单次录音流程测试。"""

from __future__ import annotations

from nextgen.apps.glass.runtime.app import GlassRuntimeApp


def test_glass_runtime_recording_flow_updates_state(monkeypatch) -> None:
    """验证眼镜端开始录音与结束录音并发送的状态切换。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.configure_server_base_url("http://127.0.0.1:18490")

    monkeypatch.setattr(
        runtime.sensor_hub,
        "start_local_microphone_recording",
        lambda output_path: {"output_path": output_path, "status": "recording", "sample_rate": 16000},
    )
    monkeypatch.setattr(
        runtime.sensor_hub,
        "stop_local_microphone_recording",
        lambda: {"output_path": "/tmp/fake.wav", "status": "stopped", "duration_sec": 1.2},
    )

    def _fake_post_json(url, payload, timeout_sec=5.0):
        if url.endswith("/voice/sessions"):
            return {"session": {"session_id": "voice_001", "device_id": "glass-001", "mode": "push_to_talk"}}
        if url.endswith("/voice/sessions/voice_001/push-to-talk"):
            return {"session_id": "voice_001", "transcript": "今天天气怎么样"}
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("nextgen.apps.glass.runtime.app.post_json", _fake_post_json)
    monkeypatch.setattr(
        runtime,
        "_open_voice_ws_session",
        lambda voice_session_id, ws_url, mode: {
            "voice_session_id": voice_session_id,
            "mode": mode,
            "closed": False,
        },
    )

    started = runtime.start_push_to_talk_recording()
    assert runtime.runtime_state == "RECORDING"
    assert runtime.voice_sessions[started["session_id"]]["recording"] is True

    stopped = runtime.stop_push_to_talk_recording_and_dispatch(started["session_id"])
    assert runtime.runtime_state == "READY"
    assert stopped["processed"]["transcript"] == "今天天气怎么样"
    assert runtime.voice_sessions[started["session_id"]]["status"] == "processed"
    assert runtime.voice_sessions[started["session_id"]]["server_voice_session_id"] == "voice_001"


def test_glass_runtime_recording_flow_records_server_error(monkeypatch) -> None:
    """验证服务器处理失败时，眼镜端会保留错误信息。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.configure_server_base_url("http://127.0.0.1:18490")

    monkeypatch.setattr(
        runtime.sensor_hub,
        "start_local_microphone_recording",
        lambda output_path: {"output_path": output_path, "status": "recording", "sample_rate": 16000},
    )
    monkeypatch.setattr(
        runtime.sensor_hub,
        "stop_local_microphone_recording",
        lambda: {"output_path": "/tmp/fake.wav", "status": "stopped", "duration_sec": 1.2},
    )

    def _fake_post_json(url, payload, timeout_sec=5.0):
        if url.endswith("/voice/sessions"):
            return {"session": {"session_id": "voice_002", "device_id": "glass-001", "mode": "push_to_talk"}}
        if url.endswith("/voice/sessions/voice_002/push-to-talk"):
            raise TimeoutError(f"timeout after {timeout_sec}")
        raise AssertionError(f"unexpected url: {url}")

    monkeypatch.setattr("nextgen.apps.glass.runtime.app.post_json", _fake_post_json)
    monkeypatch.setattr(
        runtime,
        "_open_voice_ws_session",
        lambda voice_session_id, ws_url, mode: {"voice_session_id": voice_session_id, "mode": mode, "closed": False},
    )

    started = runtime.start_push_to_talk_recording()
    try:
        runtime.stop_push_to_talk_recording_and_dispatch(started["session_id"])
        raise AssertionError("expected timeout")
    except TimeoutError:
        pass

    assert runtime.voice_sessions[started["session_id"]]["status"] == "failed"
    assert "timeout after 120.0" in runtime.voice_sessions[started["session_id"]]["error"]


def test_glass_runtime_tts_done_returns_to_ready() -> None:
    """验证眼镜端在 TTS 完成后回到 READY。"""

    runtime = GlassRuntimeApp()
    runtime.start()
    runtime.runtime_state = "PLAYING"
    runtime.voice_sessions["voice_001"] = {"mode": "push_to_talk", "status": "processed"}

    runtime._handle_voice_server_message("voice_001", {"type": "tts.done", "session_id": "voice_001"})

    assert runtime.runtime_state == "READY"
