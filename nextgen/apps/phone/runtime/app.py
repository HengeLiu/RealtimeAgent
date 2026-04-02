"""手机端运行时应用骨架。"""

from dataclasses import dataclass

from nextgen.apps.phone.gateway.phone_gateway import PhoneGateway
from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.apps.phone.tasks.find_object_task import FindObjectTask
from nextgen.apps.phone.tasks.local_task_center import LocalTaskCenter

@dataclass
class PhoneRuntimeApp:
    """手机端运行时应用。"""

    name: str = "phone-runtime"

    def start(self) -> None:
        """启动手机端运行时。

        主要逻辑：
        - 当前阶段完成最小模块装配，便于后续扩展真实连接与任务装配。
        """

        self.gateway = PhoneGateway()
        self.local_task_center = LocalTaskCenter()
        self.find_object_task = FindObjectTask(target_name="未设置")
        self.object_detection_skill = ObjectDetectionSkill()

    def stop(self) -> None:
        """停止手机端运行时。"""
