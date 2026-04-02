"""智能体中心实现。"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from nextgen.apps.server.task_center.background_task_center import BackgroundTaskCenter


@dataclass
class AgentCenter:
    """智能体中心。

    主要功能：
    - 理解用户意图
    - 决定是否调用技能
    - 回答任务状态问题
    """

    task_center: Optional[BackgroundTaskCenter] = None

    def interpret(self, text: str) -> Dict[str, Any]:
        """解释用户输入。

        当前阶段采用轻量规则：
        - 包含“找”时优先判断为找物任务
        - 包含“状态”“进度”时判断为任务状态查询
        """

        normalized = (text or "").strip()
        if not normalized:
            return {"intent": "empty", "raw_text": text}

        if any(keyword in normalized for keyword in ["状态", "进度", "怎么样", "还在吗"]):
            return {"intent": "query_task_status", "raw_text": text}

        if "找" in normalized:
            target = normalized.replace("帮我", "").replace("找一下", "").replace("找", "").strip(" ，。")
            return {
                "intent": "create_hybrid_task",
                "task_name": "find_object",
                "params": {"target_name": target or "未指定目标"},
                "raw_text": text,
            }

        return {
            "intent": "unknown",
            "raw_text": text,
        }

    def answer_task_status(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        """回答任务状态问题。"""

        if self.task_center is None:
            return {"answer": "当前未接入任务中心，无法查询任务状态。"}

        if session_id:
            session = self.task_center.get_session(session_id)
            if session is None:
                return {"answer": f"未找到任务 {session_id}。"}
            return {
                "answer": f"任务 {session.task_name} 当前状态为 {session.status.value}，阶段为 {session.phase}。",
                "session": session.to_dict(),
            }

        sessions = self.task_center.list_sessions()
        if not sessions:
            return {"answer": "当前没有正在跟踪的任务。"}

        latest = sorted(sessions, key=lambda item: item.updated_at)[-1]
        return {
            "answer": f"最近任务 {latest.task_name} 当前状态为 {latest.status.value}，阶段为 {latest.phase}。",
            "session": latest.to_dict(),
        }
