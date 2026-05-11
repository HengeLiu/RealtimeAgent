from __future__ import annotations

import logging

from audio_chat.observability import RunRecorder
from audio_chat.protocol import Event, StreamChunk


def test_run_recorder_logs_artifact_index_once_on_startup(tmp_path, caplog) -> None:
    """测试目标：验证运行产物目录只在记录器启动时集中提示一次。

    测试方法：在 caplog 捕获范围内创建 RunRecorder，并检查启动索引日志。
    预期结果：日志包含 runs 根目录、全局文件和会话文件索引，不依赖后续事件重复打印路径。
    """

    with caplog.at_level(logging.INFO, logger="audio_chat.runs"):
        RunRecorder(tmp_path / "runs")

    index_logs = [record for record in caplog.records if "运行产物目录索引" in record.getMessage()]
    assert len(index_logs) == 1
    assert getattr(index_logs[0], "runs_root") == str(tmp_path / "runs")
    assert "system-events.jsonl" in getattr(index_logs[0], "global_files")
    assert "model-request.json" in getattr(index_logs[0], "session_files")


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


def test_realtime_first_model_request_terminal_snapshot_only_logs_model_visible_messages(tmp_path, caplog) -> None:
    """测试目标：验证 Realtime 首次模型请求终端日志只打印模型可见上下文。

    测试方法：构造同时包含 `instructions`、`history_messages` 和规范化 `messages`
    的 Realtime 请求，并捕获首次快照日志。
    预期结果：终端日志包含 `messages/tools`，但不重复打印 Realtime 中间调试字段。
    """

    recorder = RunRecorder(tmp_path / "runs")
    request = {
        "provider": "qwen",
        "model": "qwen3.5-omni-plus-realtime",
        "runner": "agent_core_realtime_audio",
        "user_id": "user-realtime",
        "session_id": "dev-realtime",
        "instructions": "系统提示词，包含历史摘要。",
        "messages": [
            {"role": "system", "content": "系统提示词，包含历史摘要。"},
            {"role": "user", "content": [{"type": "input_audio_stream", "stream_type": "sensor.mic"}]},
        ],
        "history_messages": [{"role": "user", "content": "历史用户输入"}],
        "history_injected_to": "instructions",
        "tools": [{"type": "function", "name": "capture_photo", "parameters": {"type": "object"}}],
        "tool_count": 1,
    }

    with caplog.at_level(logging.INFO, logger="audio_chat.runs"):
        recorder.record_model_request("dev-realtime", request)

    snapshots = [record.getMessage() for record in caplog.records if "首次模型请求完整快照" in record.getMessage()]
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert '"messages"' in snapshot
    assert '"tools"' in snapshot
    assert "系统提示词，包含历史摘要。" in snapshot
    assert "input_audio_stream" in snapshot
    assert '"instructions"' not in snapshot
    assert '"history_messages"' not in snapshot
    assert '"history_injected_to"' not in snapshot


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
    assert not any(hasattr(record, "detail_path") for record in caplog.records)
    assert (tmp_path / "runs" / "system-events.jsonl").exists()


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
                "event": "stream.chunk.dropped",
                "stream_id": chunk.stream_id,
                "stream_type": chunk.stream_type,
                "seq": 3,
                "payload_size": 2,
                "reason": "input_stream_closed_late_chunk",
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
    assert not any("stream.chunk.dropped" in record.getMessage() for record in caplog.records)
    close_records = [record for record in caplog.records if "数据流事件 stream.closed" in record.getMessage()]
    assert len(close_records) == 1
    assert getattr(close_records[0], "input_chunk_count") == 3
    assert getattr(close_records[0], "input_bytes") == 6
    assert not hasattr(close_records[0], "detail_path")
    assert (tmp_path / "runs" / "_unbound" / "sess-stream-log" / "stream-events.jsonl").exists()


def test_important_terminal_logs_hide_storage_paths_but_keep_artifacts(tmp_path, caplog) -> None:
    """测试目标：重要终端日志不重复打印落盘路径，但文件仍正常写入。

    测试方法：记录模型请求和工具调用，检查终端 extra 与运行产物文件。
    预期结果：终端日志没有 detail_path/path，`model-request.json` 和 `tool-events.jsonl` 仍存在。
    """

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
    assert model_logs and not hasattr(model_logs[0], "detail_path")
    assert model_logs and not hasattr(model_logs[0], "path")
    assert tool_logs and not hasattr(tool_logs[0], "detail_path")
    assert (tmp_path / "runs" / "user-path" / "sess-path" / "model-request.json").exists()
    assert (tmp_path / "runs" / "user-path" / "sess-path" / "tool-events.jsonl").exists()
