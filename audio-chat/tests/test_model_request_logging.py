from __future__ import annotations

import logging

from audio_chat.observability import RunRecorder


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
