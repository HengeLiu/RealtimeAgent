from __future__ import annotations

import logging

from audio_chat.observability import RunRecorder
from audio_chat.protocol import Event, StreamChunk


def test_first_model_request_logs_full_snapshot_once(tmp_path, caplog) -> None:
    """测试目标：验证首次大模型调用前会在终端日志打印完整请求快照。

    测试方法：连续记录两次 model request，并用 caplog 捕获 `audio_chat.runs` 日志。
    预期结果：只有第一次包含完整 JSON，且 JSON 中能看到 system prompt、messages 和 tools。
    """

    recorder = RunRecorder(tmp_path / "runs")
    request = {
        "provider": "mock",
        "model": "mock-model",
        "runner": "agent_core_text",
        "user_id": "user-log",
        "messages": [
            {"role": "system", "content": "你是测试助手。"},
            {"role": "user", "content": "有哪些设备在线？"},
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "list_online_devices",
                    "description": "查询在线设备。",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "tool_count": 1,
    }

    with caplog.at_level(logging.INFO, logger="audio_chat.runs"):
        recorder.record_model_request("sess-001", request)
        recorder.record_model_request("sess-002", request)

    full_snapshots = [record for record in caplog.records if "首次模型请求完整快照" in record.getMessage()]
    assert len(full_snapshots) == 1
    message = full_snapshots[0].getMessage()
    assert "你是测试助手。" in message
    assert '"messages"' in message
    assert '"tools"' in message
    assert "list_online_devices" in message


def test_system_error_logs_payload_details(tmp_path, caplog) -> None:
    """测试目标：系统错误日志必须展示 payload 中的真实错误信息。

    测试方法：记录一条 `system.error.raised` 事件和对应 system event。
    预期结果：终端日志能看到 error_type、message、stream_id，不再打印 `系统事件 None`。
    """

    recorder = RunRecorder(tmp_path / "runs")
    event = Event(
        event_name="system.error.raised",
        user_id="user-log",
        producer_id="server-main",
        session_id="sess-log",
        stream_id="stream-log",
        stream_type="sensor.mic",
        payload={
            "message": "stream is not open: state=closed",
            "error_type": "StreamNotOpenError",
            "severity": "warning",
            "device_id": "dev-log",
        },
    )

    with caplog.at_level(logging.INFO, logger="audio_chat.runs"):
        recorder.record_event(event)
        recorder.record_system_event(event.to_dict())

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert "系统事件 system.error.raised" in messages
    assert "系统事件 None" not in messages
    assert any(getattr(record, "error_type", None) == "StreamNotOpenError" for record in caplog.records)
    assert any(getattr(record, "stream_id", None) == "stream-log" for record in caplog.records)
    assert any(str(tmp_path / "runs" / "system-events.jsonl") == getattr(record, "detail_path", None) for record in caplog.records)


def test_realtime_provider_error_terminal_log_keeps_actionable_message(tmp_path, caplog) -> None:
    """测试目标：Realtime provider 错误日志不能截断关键错误信息。

    测试方法：记录一条较长的 `realtime.session.failed` Agent 事件。
    预期结果：终端 extra 中保留 provider 返回的具体 message，方便现场调试。
    """

    recorder = RunRecorder(tmp_path / "runs")
    message = {
        "event_id": "event-debug",
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "message": "Error append image before append audio.",
        },
    }

    with caplog.at_level(logging.INFO, logger="audio_chat.runs"):
        recorder.record_agent_event(
            "sess-log",
            {
                "event": "realtime.session.failed",
                "provider": "qwen",
                "model": "qwen3.5-omni-plus-realtime",
                "message": str(message),
            },
        )

    error_messages = [getattr(record, "error_message", "") for record in caplog.records]
    assert any("Error append image before append audio." in item for item in error_messages)
    assert any("invalid_request_error" in item for item in error_messages)


def test_stream_chunk_terminal_logs_are_summarized_on_close(tmp_path, caplog) -> None:
    """测试目标：stream chunk 不逐条刷终端，只在关闭时输出摘要。

    测试方法：记录多个 chunk 事件后关闭同一 stream。
    预期结果：chunk 事件只落盘；终端只有关闭日志，并带 input_chunk_count/input_bytes。
    """

    recorder = RunRecorder(tmp_path / "runs")
    chunk = StreamChunk(
        user_id="user-stream-log",
        session_id="sess-stream-log",
        stream_id="stream-stream-log",
        stream_type="sensor.mic",
        seq=0,
        payload=b"\x00\x00",
    )

    with caplog.at_level(logging.INFO, logger="audio_chat.runs"):
        for seq in range(3):
            recorder.record_stream_event(
                chunk.session_id,
                {
                    "event": "stream.chunk.received",
                    "stream_id": chunk.stream_id,
                    "stream_type": chunk.stream_type,
                    "seq": seq,
                    "payload_size": 2,
                    "final": seq == 2,
                },
            )
        recorder.record_stream_event(
            chunk.session_id,
            {
                "event": "stream.closed",
                "stream_id": chunk.stream_id,
                "stream_type": chunk.stream_type,
                "reason": "completed",
            },
        )

    assert not any("stream.chunk.received" in record.getMessage() for record in caplog.records)
    close_records = [record for record in caplog.records if "数据流事件 stream.closed" in record.getMessage()]
    assert len(close_records) == 1
    assert getattr(close_records[0], "input_chunk_count") == 3
    assert getattr(close_records[0], "input_bytes") == 6
    assert getattr(close_records[0], "detail_path").endswith("sessions/sess-stream-log/stream-events.jsonl")


def test_important_terminal_logs_include_detail_paths(tmp_path, caplog) -> None:
    """测试目标：重要终端日志应提示完整细节文件位置。"""

    recorder = RunRecorder(tmp_path / "runs")
    request = {
        "provider": "mock",
        "model": "mock-model",
        "runner": "agent_core_text",
        "user_id": "user-path",
        "messages": [{"role": "system", "content": "debug"}],
        "tools": [],
    }

    with caplog.at_level(logging.INFO, logger="audio_chat.runs"):
        recorder.record_model_request("sess-path", request)
        recorder.record_tool_trace("sess-path", {"tool_name": "demo_tool", "user_id": "user-path", "ok": True})

    model_logs = [record for record in caplog.records if "模型请求已写入" in record.getMessage()]
    tool_logs = [record for record in caplog.records if "工具调用 demo_tool" in record.getMessage()]
    assert model_logs and getattr(model_logs[0], "detail_path").endswith("sessions/sess-path/model-request.json")
    assert tool_logs and getattr(tool_logs[0], "detail_path").endswith("sessions/sess-path/tool-events.jsonl")
