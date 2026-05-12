import json
from pathlib import Path

import yaml

from audio_chat_python_playback_glass.recorder import RecordOptions, record_case


def test_recorder_generates_stable_case_without_dynamic_ids(tmp_path: Path) -> None:
    """测试目标：验证 recorder 从 runs 产物生成稳定 Case 草稿。

    测试方法：构造最小 session 产物，执行 `record_case`，检查 YAML 不包含动态字段。
    预期结果：生成 Case 保留事件、stream、工具和资产断言，但不写入 stream_id。
    """

    runs_root = tmp_path / "runs"
    session_dir = runs_root / "sessions" / "sess-001"
    session_dir.mkdir(parents=True)
    _write_jsonl(
        session_dir / "events.jsonl",
        [
            {"event_name": "control.device.registered", "event_id": "evt-1", "timestamp_ms": 1},
            {"event_name": "stream.control.open.requested", "stream_id": "stream-dynamic", "stream_type": "sensor.rgb"},
        ],
    )
    _write_jsonl(session_dir / "stream-events.jsonl", [{"stream_type": "sensor.rgb", "stream_id": "stream-dynamic"}])
    _write_jsonl(session_dir / "tool-events.jsonl", [{"tool_name": "capture_photo"}])
    _write_jsonl(session_dir / "assets.jsonl", [{"stream_type": "sensor.rgb", "asset_id": "asset-dynamic"}])
    (session_dir / "model-request.json").write_text(json.dumps({"tools": [{"function": {"name": "capture_photo"}}]}), encoding="utf-8")
    _write_jsonl(runs_root / "system-events.jsonl", [])
    out = tmp_path / "case.yaml"

    data = record_case(
        RecordOptions(
            runs_root=runs_root,
            user_id="user-browser-glass-001",
            device_id="dev-browser-glass-001",
            session_id="sess-001",
            audio="testdata/audio-sample/看一下我前面有什么.wav",
            images={"sensor.rgb": "testdata/image-sample/刚子看电脑.jpeg"},
            out=out,
        )
    )

    text = out.read_text(encoding="utf-8")
    parsed = yaml.safe_load(text)
    assert data["expect"]["tools"]["called"] == ["capture_photo"]
    assert parsed["expect"]["assets"]["sensor.rgb"]["min_count"] == 1
    assert "stream-dynamic" not in text
    assert "asset-dynamic" not in text
    assert "evt-1" not in text


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    """写入测试 JSONL。"""

    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")
