"""任务级数据面消息模型。"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from nextgen.shared.models.detection import FindObjectFrameAnalysis, HintPayload
from nextgen.shared.models.execution import ExecutionFeedback


@dataclass
class FindObjectFrameMessage:
    """寻找物体单帧消息。

    主要功能：
    - 描述眼镜通过任务级数据面发送给手机的一次单帧分析输入
    - 当前阶段先发送结构化分析输入，而不是原始图像字节
    """

    task_session_id: str
    analysis: FindObjectFrameAnalysis
    mark_completed: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """将单帧消息转换为字典。"""

        return {
            "task_session_id": self.task_session_id,
            "analysis": self.analysis.to_dict(),
            "mark_completed": self.mark_completed,
        }


@dataclass
class GuidanceHintMessage:
    """引导建议消息。

    主要功能：
    - 描述手机通过任务级数据面直接发给眼镜的引导建议
    """

    task_session_id: str
    hint: HintPayload
    state_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将引导建议消息转换为字典。"""

        return {
            "task_session_id": self.task_session_id,
            "hint": self.hint.to_dict(),
            "state_summary": self.state_summary,
        }


@dataclass
class GuidanceExecutionMessage:
    """引导执行结果消息。

    主要功能：
    - 描述眼镜执行引导后的结果
    - 用于眼镜向服务器汇报执行情况
    """

    task_session_id: str
    hint_text: str
    execution_feedback: ExecutionFeedback
    state_summary: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """将引导执行消息转换为字典。"""

        return {
            "task_session_id": self.task_session_id,
            "hint_text": self.hint_text,
            "execution_feedback": self.execution_feedback.to_dict(),
            "state_summary": self.state_summary or {},
        }
