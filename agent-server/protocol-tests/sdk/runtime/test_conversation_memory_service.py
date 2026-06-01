from __future__ import annotations

import json
import logging

from realtime_agent.conversation import ConversationMemoryService


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


class FixedSummarizer:
    """测试用摘要器。

    主要功能：记录传入的上一版摘要和消息数量，并返回固定结构化摘要。
    主要属性：`calls` 保存调用参数，便于断言增量摘要输入。
    """

    def __init__(self, content: str = "用户身份与偏好：\n- 用户叫文刀。\n当前对话状态：\n- 已确认身份。") -> None:
        self.content = content
        self.calls: list[dict] = []

    def summarize(self, *, previous_summary: str, messages: list[dict]) -> str:
        """返回固定摘要。"""

        self.calls.append({"previous_summary": previous_summary, "message_count": len(messages)})
        return self.content


class FailingSummarizer:
    """测试用失败摘要器。"""

    def summarize(self, *, previous_summary: str, messages: list[dict]) -> str:
        """模拟 LLM 摘要失败。"""

        raise RuntimeError("llm unavailable")


def test_conversation_memory_service_compacts_active_messages(tmp_path) -> None:
    """测试目标：验证消息维护服务按 active/history/summary 三层保存对话。

    测试方法：写入 32 条 active messages 后触发压缩，保留最新 5 条。
    预期结果：内存 active 和 messages 备份只剩 5 条；history 归档 27 条；summary 可进入提示词。
    """

    summarizer = FixedSummarizer()
    service = ConversationMemoryService(tmp_path / "runs", summarizer=summarizer)
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
    active = service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)
    legacy = _read_jsonl(device_dir / "messages.jsonl")
    history_files = list((device_dir / "history").glob("*-messages.jsonl"))
    summaries = _read_jsonl(device_dir / "message-summaries.jsonl")
    prompt_fragment = service.build_summary_prompt_fragment(user_id=user_id, device_id=device_id)

    assert summary is not None
    assert len(active) == 5
    assert len(legacy) == 5
    assert not (device_dir / "active-messages.jsonl").exists()
    assert len(history_files) == 1
    assert len(_read_jsonl(history_files[0])) == 27
    assert summaries[-1]["source_message_count"] == 27
    assert "用户身份与偏好" in summaries[-1]["content"]
    assert summarizer.calls[-1]["message_count"] == 27
    assert "更早历史对话的压缩摘要" in prompt_fragment
    assert "不代表当前图片、当前画面或当前传感器状态" in prompt_fragment
    assert summaries[-1]["content"] in prompt_fragment


def test_conversation_memory_service_skips_compaction_when_summarizer_fails(tmp_path, caplog) -> None:
    """测试目标：验证 LLM 摘要失败时跳过压缩且保留 active 原文。

    测试方法：注入会抛异常的摘要器，写入 7 条消息后触发压缩。
    预期结果：不生成 history 和 summary；内存 active 和 messages 仍保留 7 条；终端记录错误。
    """

    service = ConversationMemoryService(tmp_path / "runs", summarizer=FailingSummarizer())
    user_id = "user-a"
    device_id = "dev-a"
    for index in range(7):
        service.append_message(
            user_id=user_id,
            device_id=device_id,
            message={"session_id": device_id, "role": "user", "content": f"第 {index} 条对话"},
        )

    with caplog.at_level(logging.ERROR, logger="realtime_agent.runs"):
        summary = service.compact_if_needed(user_id=user_id, device_id=device_id, threshold=6, keep_latest=2)

    device_dir = tmp_path / "runs" / user_id / device_id
    assert summary is None
    assert len(service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)) == 7
    assert len(_read_jsonl(device_dir / "messages.jsonl")) == 7
    assert not (device_dir / "active-messages.jsonl").exists()
    assert not (device_dir / "history").exists()
    assert not (device_dir / "message-summaries.jsonl").exists()
    assert any("会话消息摘要失败" in record.getMessage() for record in caplog.records)
    assert any(getattr(record, "error_message", "") == "llm unavailable" for record in caplog.records)


def test_conversation_memory_service_logs_info_when_summarizer_not_configured(tmp_path, caplog) -> None:
    """测试目标：验证未配置摘要器时跳过压缩但不记录错误。

    测试方法：不注入摘要器，写入超过阈值的消息后触发压缩。
    预期结果：不生成 history 和 summary；active 原文保持不变；终端只记录 INFO 级别跳过事件。
    """

    service = ConversationMemoryService(tmp_path / "runs", summarizer=None)
    user_id = "user-a"
    device_id = "dev-a"
    for index in range(7):
        service.append_message(
            user_id=user_id,
            device_id=device_id,
            message={"session_id": device_id, "role": "user", "content": f"第 {index} 条对话"},
        )

    with caplog.at_level(logging.INFO, logger="realtime_agent.runs"):
        summary = service.compact_if_needed(user_id=user_id, device_id=device_id, threshold=6, keep_latest=2)

    device_dir = tmp_path / "runs" / user_id / device_id
    assert summary is None
    assert len(service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)) == 7
    assert len(_read_jsonl(device_dir / "messages.jsonl")) == 7
    assert not (device_dir / "history").exists()
    assert not (device_dir / "message-summaries.jsonl").exists()
    assert any("会话消息摘要未配置" in record.getMessage() for record in caplog.records)
    assert not any(record.levelno >= logging.ERROR for record in caplog.records)


def test_conversation_memory_service_restores_active_messages_from_full_messages(tmp_path) -> None:
    """测试目标：验证重启后可从完整 messages.jsonl 恢复内存 active messages。

    测试方法：只写完整 messages 文件，然后调用 active 读取接口。
    预期结果：返回可进入模型上文的旧消息，但不创建离线 active 文件。
    """

    service = ConversationMemoryService(tmp_path / "runs")
    user_id = "user-a"
    device_id = "dev-a"
    device_dir = tmp_path / "runs" / user_id / device_id
    device_dir.mkdir(parents=True)
    legacy_records = [
        {"session_id": device_id, "role": "user", "content": "旧消息 1"},
        {"session_id": device_id, "role": "assistant", "content": ""},
        {"session_id": device_id, "role": "tool", "content": {"ok": True}},
        {
            "session_id": device_id,
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "city_lookup", "arguments": {"city": "上海"}}],
        },
        {"session_id": device_id, "role": "tool", "tool_call_id": "call-1", "name": "city_lookup", "content": {"ok": True}},
        {"session_id": device_id, "role": "assistant", "content": "旧消息 2"},
    ]
    (device_dir / "messages.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in legacy_records),
        encoding="utf-8",
    )

    active = service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)

    assert [item["role"] for item in active] == ["user", "assistant", "tool", "assistant"]
    assert active[1]["tool_calls"][0]["name"] == "city_lookup"
    assert active[2]["tool_call_id"] == "call-1"
    assert active[3]["content"] == "旧消息 2"
    assert _read_jsonl(device_dir / "messages.jsonl") == legacy_records
    assert not (device_dir / "active-messages.jsonl").exists()


def test_conversation_memory_service_keeps_full_messages_and_visible_active_separate(tmp_path) -> None:
    """测试目标：验证完整审计消息和模型可见 active messages 分开维护。

    测试方法：追加 user、空 assistant、成对 tool_call/tool_result 和 assistant 文本消息。
    预期结果：messages 保存全部消息；内存 active 保留可合法回灌的工具调用过程。
    """

    service = ConversationMemoryService(tmp_path / "runs")
    user_id = "user-a"
    device_id = "dev-a"
    service.append_message(user_id=user_id, device_id=device_id, message={"session_id": device_id, "role": "user", "content": "你好"})
    service.append_message(user_id=user_id, device_id=device_id, message={"session_id": device_id, "role": "assistant", "content": ""})
    service.append_message(
        user_id=user_id,
        device_id=device_id,
        message={
            "session_id": device_id,
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "city_lookup", "arguments": {"city": "上海"}}],
        },
    )
    service.append_message(
        user_id=user_id,
        device_id=device_id,
        message={
            "session_id": device_id,
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "city_lookup",
            "content": {"ok": True},
        },
    )
    service.append_message(user_id=user_id, device_id=device_id, message={"session_id": device_id, "role": "assistant", "content": "你好"})

    device_dir = tmp_path / "runs" / user_id / device_id
    active = service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)
    legacy = _read_jsonl(device_dir / "messages.jsonl")
    assert [item["role"] for item in active] == ["user", "assistant", "tool", "assistant"]
    assert active[1]["tool_calls"][0]["name"] == "city_lookup"
    assert active[2]["tool_call_id"] == "call-1"
    assert len(legacy) == 5
    assert [item["role"] for item in legacy] == ["user", "assistant", "assistant", "tool", "assistant"]
    assert not (device_dir / "active-messages.jsonl").exists()


def test_conversation_memory_service_keeps_omni_tool_records_audit_only(tmp_path) -> None:
    """测试目标：确认 Text 历史修复不会改变 Omni Realtime 工具审计语义。

    测试方法：写入 `source=omni_realtime` 的 assistant tool_call 和 tool result。
    预期结果：messages.jsonl 保存完整审计，但 active history 不把 Omni 工具过程回灌。
    """

    service = ConversationMemoryService(tmp_path / "runs")
    user_id = "user-a"
    device_id = "dev-a"
    records = [
        {"session_id": device_id, "role": "user", "content": "你好"},
        {
            "session_id": device_id,
            "role": "assistant",
            "content": "",
            "source": "omni_realtime",
            "tool_calls": [{"id": "call-omni", "name": "capture_photo", "arguments": {}}],
        },
        {
            "session_id": device_id,
            "role": "tool",
            "source": "omni_realtime",
            "tool_call_id": "call-omni",
            "name": "capture_photo",
            "content": {"ok": True},
        },
        {"session_id": device_id, "role": "assistant", "content": "已看完。"},
    ]
    for record in records:
        service.append_message(user_id=user_id, device_id=device_id, message=record)

    active = service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)
    legacy = _read_jsonl(tmp_path / "runs" / user_id / device_id / "messages.jsonl")

    assert [item["role"] for item in active] == ["user", "assistant"]
    assert [item["content"] for item in active] == ["你好", "已看完。"]
    assert len(legacy) == 4
    assert any(item.get("source") == "omni_realtime" for item in legacy)


def test_conversation_memory_service_compacts_full_message_backup_with_tool_records(tmp_path) -> None:
    """测试目标：验证压缩时完整工具调用过程随旧 active 一起进入 history。

    测试方法：写入 user、空 assistant tool_call、tool result、assistant 等混合消息，
    按 4 条模型可见消息触发压缩并保留最新 1 条。
    预期结果：history 保存被压缩部分的完整调用过程；messages 只保留剩余 active 的备份。
    """

    summarizer = FixedSummarizer()
    service = ConversationMemoryService(tmp_path / "runs", summarizer=summarizer)
    user_id = "user-a"
    device_id = "dev-a"
    records = [
        {"session_id": device_id, "role": "user", "content": "用户 1", "created_at": 1_700_000_001},
        {
            "session_id": device_id,
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call-1", "name": "city_lookup", "arguments": {"city": "上海"}}],
            "event": "assistant_tool_call.done",
            "created_at": 1_700_000_002,
        },
        {
            "session_id": device_id,
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "city_lookup",
            "content": {"ok": True},
            "event": "tool_result.done",
            "created_at": 1_700_000_003,
        },
        {"session_id": device_id, "role": "assistant", "content": "助手 1", "created_at": 1_700_000_004},
        {"session_id": device_id, "role": "user", "content": "用户 2", "created_at": 1_700_000_005},
        {"session_id": device_id, "role": "assistant", "content": "助手 2", "created_at": 1_700_000_006},
    ]
    for record in records:
        service.append_message(user_id=user_id, device_id=device_id, message=record)

    summary = service.compact_if_needed(user_id=user_id, device_id=device_id, threshold=3, keep_latest=1)

    device_dir = tmp_path / "runs" / user_id / device_id
    history_file = next((device_dir / "history").glob("*-messages.jsonl"))
    history = _read_jsonl(history_file)
    messages = _read_jsonl(device_dir / "messages.jsonl")
    active = service.load_active_messages(user_id=user_id, device_id=device_id, limit=10)
    assert summary is not None
    assert [item["event"] for item in history if item.get("event")] == ["assistant_tool_call.done", "tool_result.done"]
    assert [item["content"] for item in active] == ["助手 2"]
    assert [item["content"] for item in messages] == ["助手 2"]
    assert summarizer.calls[-1]["message_count"] == 5
