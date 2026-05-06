from pathlib import Path

from audio_chat.endpoints import run_playback


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
    assert list(session_dir.glob("input-*.pcm"))
    assert list(session_dir.glob("output-*.pcm"))
