from __future__ import annotations

import asyncio

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.memory import JsonlMemoryStore, MemoryService


def test_memory_service_writes_searches_and_deletes_jsonl_records(tmp_path) -> None:
    """测试目标：验证 Memory Service 的 jsonl 存储闭环。

    测试方法：启用 Memory Service，写入两条用户记忆，按关键词搜索后删除其中一条。
    预期结果：搜索只能返回当前用户的有效记录，删除后的记录不再出现。
    """

    service = MemoryService(enabled=True, store=JsonlMemoryStore(tmp_path / "memory"))

    first = service.write(user_id="user-001", content="白色水杯在书桌左侧", metadata={"source": "test"})
    service.write(user_id="user-001", content="手机通常放在玄关", metadata={"source": "test"})
    service.write(user_id="user-002", content="白色水杯在厨房", metadata={"source": "other-user"})

    matches = service.search(user_id="user-001", query="水杯", limit=10)
    assert [item.content for item in matches] == ["白色水杯在书桌左侧"]

    assert service.delete(user_id="user-001", memory_id=first.memory_id) is True
    assert service.search(user_id="user-001", query="水杯", limit=10) == []


def test_memory_disabled_does_not_expose_memory_tools(tmp_path) -> None:
    """测试目标：验证 memory.enabled=false 不影响现有内置工具。

    测试方法：创建默认 App，读取 ToolRegistry 中的工具名。
    预期结果：基础工具仍存在，memory_search 和 manage_memory 不暴露给 Agent。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), memory_enabled=False))

    names = app.tool_registry.list_names()
    assert "query_device_state" in names
    assert "memory_search" not in names
    assert "manage_memory" not in names


def test_memory_enabled_exposes_and_executes_builtin_tools(tmp_path) -> None:
    """测试目标：验证启用 memory 后内置 Tool 可被 Agent 调用。

    测试方法：通过 ToolGateway 调用 manage_memory 和 memory_search。
    预期结果：写入结果成功，随后搜索能返回同一条记忆。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            memory_enabled=True,
            memory_path=str(tmp_path / "memory"),
        )
    )

    assert {"manage_memory", "memory_search"}.issubset(set(app.tool_registry.list_names()))
    write_result = asyncio.run(
        app.tool_gateway.call(
            name="manage_memory",
            user_id="user-memory",
            session_id="session-memory",
            input_data={"content": "电梯口在走廊尽头", "metadata": {"kind": "location"}},
        )
    )
    search_result = asyncio.run(
        app.tool_gateway.call(
            name="memory_search",
            user_id="user-memory",
            session_id="session-memory",
            input_data={"query": "电梯", "limit": 3},
        )
    )

    assert write_result.ok is True
    assert search_result.ok is True
    assert search_result.data[0]["content"] == "电梯口在走廊尽头"
