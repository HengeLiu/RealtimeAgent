from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from audio_chat.agent_core.context import ContextCompileRequest, ContextCompiler


class FakeMemory:
    """测试用 MemoryService。"""

    enabled = True

    def build_prompt_fragment(self, *, user_id: str) -> str:
        """返回固定长期记忆片段。"""

        return f"用户 {user_id} 喜欢简短回答。"


class FakeControl:
    """测试用 ControlService。"""

    def load_messages(self, *, user_id: str, session_id: str, limit: int) -> list[dict[str, Any]]:
        """返回包含 tool 审计消息的历史。"""

        return [
            {"role": "user", "content": "上一轮问题"},
            {"role": "tool", "content": {"ok": True}},
            {"role": "assistant", "content": "上一轮回答"},
        ]

    def load_message_summary_fragment(self, *, user_id: str, session_id: str) -> str:
        """返回固定历史摘要。"""

        return "当前对话状态：\n- 已完成上一轮问答。"


@dataclass
class FakeToolGateway:
    """测试用 ToolGateway。"""

    schemas: list[dict[str, Any]]

    def provider_schemas(self) -> list[dict[str, Any]]:
        """返回 provider schema。"""

        return list(self.schemas)


def test_text_context_compiler_records_sources_and_excludes_tool_history() -> None:
    """测试目标：验证 text 上下文由 ContextCompiler 统一生成。

    测试方法：注入 fake memory、history 和工具 schema。
    预期结果：instructions 包含记忆和摘要，messages 不包含孤立 tool history，source map 完整。
    """

    context = ContextCompiler().compile(
        ContextCompileRequest(
            mode="text",
            provider="mock",
            model="mock-text",
            user_id="user-1",
            session_id="dev-1",
            base_instructions="系统提示",
            current_input={"type": "text", "transcript": "当前问题"},
            memory_service=FakeMemory(),
            control_service=FakeControl(),
            tool_gateway=FakeToolGateway(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "lookup_weather",
                            "description": "查询天气",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ]
            ),
        )
    )

    assert "系统提示" in context.instructions
    assert "喜欢简短回答" in context.instructions
    assert "当前对话状态" in context.instructions
    assert [message["role"] for message in context.messages] == ["user", "assistant", "user"]
    assert context.messages[-1]["content"] == "当前问题"
    assert context.tools[0]["function"]["name"] == "lookup_weather"
    source_names = {source.source_name for source in context.context_sources}
    assert {"text_system", "long_term_memory", "history_summary", "active_messages", "current_input", "tool_schema"} <= source_names


def test_realtime_context_compiler_filters_inline_vision_tools() -> None:
    """测试目标：验证 realtime 上下文过滤 inline vision 工具。

    测试方法：同时提供 `capture_photo` 和普通工具 schema。
    预期结果：Realtime tools 只保留普通工具，并记录 realtime 工具调用规则 prompt。
    """

    context = ContextCompiler().compile(
        ContextCompileRequest(
            mode="realtime_audio",
            provider="qwen",
            model="qwen-realtime",
            user_id="user-1",
            session_id="dev-1",
            base_instructions="实时系统提示",
            current_input={"type": "input_audio_stream", "stream_type": "sensor.mic"},
            include_realtime_tool_rules=True,
            tool_gateway=FakeToolGateway(
                [
                    {
                        "type": "function",
                        "function": {
                            "name": "capture_photo",
                            "description": "抓拍",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    },
                    {
                        "type": "function",
                        "function": {
                            "name": "task_runtime_manager",
                            "description": "管理任务",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    },
                ]
            ),
        )
    )

    assert "不要先向用户播报" in context.instructions
    assert [tool["name"] for tool in context.tools] == ["task_runtime_manager"]
    assert context.modal_inputs[0]["type"] == "input_audio_stream"
    assert any(source.source_name == "realtime_tool_call_rules" for source in context.context_sources)
