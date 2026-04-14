"""agent-core 输入上下文装配器。"""

from __future__ import annotations

from agent_core.context.models import AgentSession, AgentTurn


class ContextAssembler:
    """最小上下文装配器。

    主要功能：
    1. 将短期消息历史、当前轮输入和引用摘要装配成 Agent 可读输入。
    2. 控制首版输入规模，避免在 Phase D 过早引入复杂压缩逻辑。
    """

    def assemble_turn_input(self, *, session: AgentSession, turn: AgentTurn, history_limit: int = 6) -> str:
        """装配单轮输入文本。

        主要逻辑：
        1. 提取最近若干条历史消息。
        2. 排除当前 turn 对应的实时用户消息，避免重复。
        3. 拼出统一的“历史 + 当前轮 + 资源摘要”文本块。

        参数：
        1. `session`：当前会话对象。
        2. `turn`：当前轮输入对象。
        3. `history_limit`：保留的历史消息数量。

        返回值：
        1. 可直接送入 Agent 的输入文本。
        """

        history_lines: list[str] = []
        history_messages = []
        for message in session.messages:
            if message.meta.get("turn_id") == turn.turn_id:
                continue
            history_messages.append(message)
        history_messages = history_messages[-history_limit:]

        for message in history_messages:
            role = {
                "user": "用户",
                "assistant": "助手",
                "tool": "工具",
                "system": "系统",
            }.get(message.role, message.role)
            history_lines.append(f"{role}: {message.text}")

        asset_lines = [
            f"- {asset.asset_type}: {asset.storage_uri}"
            for asset in turn.asset_refs
        ]
        artifact_lines = [
            f"- {artifact.artifact_type}: {artifact.text}"
            for artifact in turn.derived_artifacts
        ]

        sections = [
            "你正在处理一轮来自智能眼镜的中文语音输入。",
            "【最近对话历史】",
            "\n".join(history_lines) if history_lines else "暂无历史消息",
            "【当前轮输入】",
            turn.input_text,
            "【当前轮媒体资产】",
            "\n".join(asset_lines) if asset_lines else "无",
            "【当前轮派生结果】",
            "\n".join(artifact_lines) if artifact_lines else "无",
        ]
        return "\n".join(sections)
