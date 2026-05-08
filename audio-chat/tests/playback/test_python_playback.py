import wave
from pathlib import Path

from audio_chat_python_glass.playback import load_wav_audio, run_playback


def test_python_playback_minimal_loop_writes_runs_artifacts(tmp_path: Path) -> None:
    result = run_playback(
        {
            "runs_root": str(tmp_path / "runs"),
            "user_id": "user-playback",
            "device_id": "dev-playback",
        }
    )

    assert result["output_chunk_count"] > 0
    names = result["event_names"]
    assert "control.device.registered" in names
    assert "control.audio_session.open.requested" in names
    assert "stream.output.open.requested" in names
    assert "stream.output.close.requested" in names
    assert "control.audio_session.close.requested" in names
    assert result["passed"] is True

    session_dir = tmp_path / "runs" / "sessions" / result["session_id"]
    assert (session_dir / "events.jsonl").exists()
    assert (session_dir / "stream-events.jsonl").exists()
    assert (session_dir / "agent-events.jsonl").exists()
    assert (session_dir / "model-events.jsonl").exists()
    assert (session_dir / "playback-result.json").exists()
    assert (session_dir / "result.json").exists()
    assert list(session_dir.glob("input-*.pcm"))
    assert list(session_dir.glob("output-*.pcm"))


def test_python_playback_accepts_recorded_wav_input(tmp_path: Path) -> None:
    """测试目标：验证 python-glass playback 可以用老 SDK 录制的 WAV 作为麦克风输入。

    测试方法：读取 `openaiglass-sdk/testdata/audio-sample/wav` 中的真实样例，通过
    in-process playback 上传给 server。
    预期结果：回放结果记录 WAV 路径、chunk 数和总字节数，落盘 input PCM 与 WAV
    数据区完全一致。
    """

    wav_path = Path("openaiglass-sdk/testdata/audio-sample/wav/看一下我前面有什么.wav")
    audio = load_wav_audio(wav_path)
    result = run_playback(
        {
            "runs_root": str(tmp_path / "runs"),
            "user_id": "user-wav-playback",
            "device_id": "dev-wav-playback",
            "audio_wav": str(wav_path),
        }
    )

    assert result["passed"] is True
    assert result["input_audio"]["source_path"].endswith("看一下我前面有什么.wav")
    assert result["input_audio"]["chunk_count"] == audio.chunk_count
    assert result["input_audio"]["total_bytes"] == audio.total_bytes
    assert result["input_audio"]["chunk_count"] > 1

    session_dir = tmp_path / "runs" / "sessions" / result["session_id"]
    input_pcm = b"".join(path.read_bytes() for path in session_dir.glob("input-*.pcm"))
    with wave.open(audio.source_path, "rb") as wav_file:
        expected_pcm = wav_file.readframes(wav_file.getnframes())
    assert input_pcm == expected_pcm
