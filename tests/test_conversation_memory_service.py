from __future__ import annotations

import json

from audio_chat.conversation import ConversationMemoryService


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_conversation_memory_service_compacts_active_messages(tmp_path) -> None:
    """测试目标：验证消息维护服务按 active/history/summary 三层保存对话。

    测试方法：写入 32 条 active messages 后触发压缩，保留最新 5 条。
    预期结果：active 和旧 messages 镜像只剩 5 条；history 归档 27 条；summary 可进入提示词。
    """

    service = ConversationMemoryService(tmp_path / "runs")
    user_id = "user-a"
    device_id = "dev-a"
    for index in range(32):
        service.append_message(
            user_id=user_id,
            device_id=device_id,
            message={
                "session_id": device_id,
                "role": "user" if index % 2 == 0 else "assistant",
                "content": f"第 {index} 条对话",
                "created_at": 1_700_000_000 + index,
            },
        )

    summary = service.compact_if_needed(user_id=user_id, device_id=device_id, threshold=30, keep_latest=5)

    device_dir = tmp_path / "runs" / user_id / device_id
    active = _read_jsonl(device_dir / "active-messages.jsonl")
    legacy = _read_jsonl(device_dir / "messages.jsonl")
    history_files = list((device_dir / "history").glob("*-messages.jsonl"))
    summaries = _read_jsonl(device_dir / "message-summaries.jsonl")
    prompt_fragment = service.build_summary_prompt_fragment(user_id=user_id, device_id=device_id)

    assert summary is not None
    assert len(active) == 5
    assert active == legacy
    assert len(history_files) == 1
    assert len(_read_jsonl(history_files[0])) == 27
    assert summaries[-1]["source_message_count"] == 27
    assert "第 0 条对话" in summaries[-1]["content"]
    assert "更早历史对话的压缩摘要" in prompt_fragment
    assert summaries[-1]["content"] in prompt_fragment


def test_conversation_memory_service_migrates_legacy_messages(tmp_path) -> None:
    """测试目标：验证旧版 messages.jsonl 能自动迁移成 active-messages.jsonl。

    测试方法：只写旧 messages 文件，然后调用 active 读取接口。
    预期结果：返回旧消息，并生成 canonical active 文件。
    """

    service = ConversationMemoryService(tmp_path / "runs")
    user_id = "user-a"
    device_id = "dev-a"
    device_dir = tmp_path / "runs" / user_id / device_id
    device_dir.mkdir(parents=True)
    legacy_records = [
        {"session_id": device_id, "role": "user", "content": "旧消息 1"},
        {"session_id": device_id, "role": "assistant", "content": "旧消息 2"},
    ]
    (device_dir / "messages.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in legacy_records),
        encoding="utf-8",
    )

    active = service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)

    assert active == legacy_records
    assert _read_jsonl(device_dir / "active-messages.jsonl") == legacy_records
