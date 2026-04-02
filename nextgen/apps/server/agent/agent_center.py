"""智能体中心骨架实现。"""


class AgentCenter:
    """智能体中心。

    主要功能：
    - 理解用户意图
    - 调用技能
    - 回答任务状态问题

    当前阶段：
    - 只提供最小意图解释占位方法。
    """

    def interpret(self, text: str) -> dict:
        """解释用户输入。

        参数：
        - text：用户输入文本。

        返回值：
        - 当前阶段返回一个最小占位结构。
        """

        return {
            "intent": "unknown",
            "raw_text": text,
        }
