"""寻找物体任务组件实现。"""

from dataclasses import dataclass

from nextgen.apps.phone.skills.object_detection_skill import ObjectDetectionSkill
from nextgen.shared.models.detection import DetectionResult, FindObjectFrameAnalysis, HintPayload


@dataclass
class FindObjectTask:
    """寻找物体任务组件。

    主要功能：
    - 接收目标检测结果
    - 生成最小引导建议

    当前阶段：
    - 已承接基础引导建议生成逻辑
    - 不实现真实检测循环和媒体通道管理
    """

    target_name: str
    phase: str = "waiting_stream"
    detection_skill: ObjectDetectionSkill = ObjectDetectionSkill()

    def update_from_detection(self, result: DetectionResult) -> HintPayload:
        """根据检测结果生成引导建议。

        主要逻辑：
        - 若检测到目标，则优先使用检测结果中的引导方向
        - 若未检测到目标，则给出继续扫描提示
        - 任务组件只负责生成建议，不负责把建议上报到服务器

        参数：
        - result：目标检测结果。

        返回值：
        - 面向用户的引导建议对象。
        """

        if result.found:
            self.phase = "guiding"
            guidance_direction = result.extra.get("guidance_direction")
            secondary_direction = result.extra.get("secondary_direction")

            if guidance_direction == "向前":
                text = f"已发现{self.target_name}，请向前靠近"
            elif guidance_direction == "保持":
                text = f"已发现{self.target_name}，目标基本居中，请保持当前方向"
            elif guidance_direction:
                text = f"已发现{self.target_name}，请{guidance_direction}"
            else:
                text = f"已发现{self.target_name}，位置：{result.position}"

            if secondary_direction and guidance_direction not in ("向前", "保持"):
                text = f"{text}，次级调整：{secondary_direction}"

            if result.extra.get("grasp_detected"):
                text = f"{text}，已检测到抓握动作"
        else:
            self.phase = "scanning"
            text = f"尚未检测到{self.target_name}，继续扫描"
        return HintPayload(
            session_id=result.session_id,
            hint_type="guidance",
            text=text,
            priority="high",
        )

    def update_from_frame_analysis(
        self,
        session_id: str,
        analysis: FindObjectFrameAnalysis,
    ) -> HintPayload:
        """根据单帧分析输入直接生成引导建议。"""

        result = self.detection_skill.detect_from_frame_analysis(
            session_id=session_id,
            analysis=analysis,
        )
        return self.update_from_detection(result)
