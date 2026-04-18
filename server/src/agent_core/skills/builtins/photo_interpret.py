"""图片解读 Skill。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from agent_core.context import generate_id
from agent_core.context.models import DerivedArtifact
from agent_core.models import CapabilityResult, SkillSpec
from agent_core.skills.base import BaseSkill
from infra.errors import ErrorCode, build_error


class PhotoInterpretInput(BaseModel):
    """图片解读输入。"""

    question: str = Field(description="用户想根据图片了解什么")
    capture_first: bool = Field(default=True, description="是否先抓拍一张照片")
    photo_asset_id: str | None = Field(default=None, description="已有图片资产编号")


class PhotoInterpretOutput(BaseModel):
    """图片解读输出。"""

    answer_text: str
    photo_asset_id: str | None = None


class PhotoInterpretSkill(BaseSkill):
    """抓拍并解读当前画面。"""

    spec = SkillSpec(
        name="photo_interpret",
        description="先抓拍当前画面，再根据图片回答用户问题",
        input_model=PhotoInterpretInput,
        output_model=PhotoInterpretOutput,
        tags=["image", "vision"],
    )

    def run(self, context, input_data: PhotoInterpretInput) -> CapabilityResult:
        asset_id = input_data.photo_asset_id
        if input_data.capture_first or not asset_id:
            if context.tool_gateway is None:
                raise build_error(ErrorCode.INVALID_CONFIG, "ToolGateway 未配置，无法触发抓拍")
            capture_result = context.tool_gateway.invoke(
                name="capture_photo",
                context=context,
                arguments={"reason": "photo_interpret"},
            )
            asset_id = str(capture_result.data.get("asset_id") or asset_id or "")

        answer = self._build_mock_answer(input_data.question)
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=context.session_id,
            artifact_type="image_interpretation",
            storage_uri=f"memory://vision/{asset_id or 'latest'}",
            text=answer,
            meta={"question": input_data.question, "photo_asset_id": asset_id},
        )
        return CapabilityResult.success(
            data={"answer_text": answer, "photo_asset_id": asset_id},
            message=answer,
            derived_artifacts=[artifact],
        )

    @staticmethod
    def _build_mock_answer(question: str) -> str:
        text = question.strip()
        if "前面" in text or "有什么" in text:
            return "我看到了一个模拟室内场景，前方有桌子、杯子和窗边的亮光。"
        if "颜色" in text:
            return "这张模拟图片整体偏浅色，主体颜色比较明亮。"
        if "障碍" in text:
            return "从模拟画面看，前方近处没有明显大障碍，但右前方像是有一张桌子。"
        return "我已经完成模拟图片解读，画面里是常见室内物品，没有发现异常。"
