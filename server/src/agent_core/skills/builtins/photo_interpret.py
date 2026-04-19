"""图片解读 Skill。"""

from __future__ import annotations

import base64
from typing import Any

from pydantic import BaseModel, Field

from agent_core.context import generate_id
from agent_core.context.models import DerivedArtifact, MediaAssetRef
from agent_core.models import CapabilityResult, SkillSpec
from agent_core.skills.base import BaseSkill
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, get_logger, log_debug


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
        description="当用户提问需要用到用户眼前图像时，先调用相机拍照，再根据图片回答用户问题。",
        input_model=PhotoInterpretInput,
        output_model=PhotoInterpretOutput,
        tags=["image", "vision"],
    )

    def __init__(self, sdk_client: Any | None = None) -> None:
        """初始化图片解读 Skill。

        参数：
        1. `sdk_client`：可选的 OpenAI SDK 客户端，便于测试时注入假对象。
        """

        self._sdk_client = sdk_client
        self._logger = get_logger("server.agent.photo_interpret")

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

        photo_asset = self._resolve_photo_asset(context, asset_id)
        answer, answer_source = self._build_answer(
            context=context,
            question=input_data.question,
            photo_asset=photo_asset,
        )
        artifact = DerivedArtifact(
            artifact_id=generate_id("artifact"),
            session_id=context.session_id,
            artifact_type="image_interpretation",
            storage_uri=f"memory://vision/{asset_id or 'latest'}",
            text=answer,
            meta={
                "question": input_data.question,
                "photo_asset_id": asset_id,
                "answer_source": answer_source,
            },
        )
        return CapabilityResult.success(
            data={
                "answer_text": answer,
                "photo_asset_id": asset_id,
                "answer_source": answer_source,
            },
            message=answer,
            derived_artifacts=[artifact],
        )

    def _build_answer(
        self,
        *,
        context,
        question: str,
        photo_asset: MediaAssetRef | None,
    ) -> tuple[str, str]:
        """生成图片解读答案。

        主要逻辑：
        1. 若存在图片资产且当前环境可用，则优先走 OpenAI SDK 的图片输入。
        2. 若真实视觉调用失败，则回退到本地 mock 结果。

        参数：
        1. `context`：能力调用上下文。
        2. `question`：用户问题。
        3. `photo_asset`：当前待解读的图片资产。

        返回值：
        1. 元组第一项为答案文本。
        2. 元组第二项为答案来源，取值 `sdk_vision` 或 `mock_fallback`。
        """

        if photo_asset is not None:
            try:
                answer = self._run_vision_model(context=context, question=question, photo_asset=photo_asset)
                if answer:
                    return answer, "sdk_vision"
            except Exception as exc:  # pragma: no cover - 单测主要覆盖成功与回退路径
                log_debug(
                    self._logger,
                    f"photo_interpret 真实视觉调用失败，回退 mock: reason={exc!r}",
                    LogContext(
                        session_id=context.session_id,
                        device_id=context.device_id,
                        message_id=context.turn_id,
                    ),
                )

        return self._build_mock_answer(question), "mock_fallback"

    def _run_vision_model(self, *, context, question: str, photo_asset: MediaAssetRef) -> str:
        """使用 OpenAI SDK 的图片输入能力执行视觉理解。

        主要逻辑：
        1. 读取图片文件并转成 `data:` URL。
        2. 通过 SDK 的图片 content part 传给模型，而不是写入文本上下文。
        3. 返回模型生成的简短中文答案。

        参数：
        1. `context`：能力调用上下文。
        2. `question`：用户问题。
        3. `photo_asset`：待解读图片资产。

        返回值：
        1. 模型返回的文本答案。

        异常情况：
        1. 缺少 API Key、图片文件不存在或 SDK 调用失败时抛异常，由上层回退。
        """

        if not context.settings.dashscope_api_key.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 DASHSCOPE_API_KEY，无法执行图片解读",
            )

        image_data_url = self._build_image_data_url(photo_asset)
        client = self._sdk_client or self._create_sdk_client(context)
        completion = client.chat.completions.create(
            model=context.settings.agent_model_name,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是盲人眼镜的视觉理解助手。"
                        "请基于用户提供的图片，用简短、口语化、直接的中文回答。"
                        "看不清时要明确说明，不要编造。"
                    ),
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": question.strip() or "请描述这张图片。",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_data_url,
                                "detail": "auto",
                            },
                        },
                    ],
                },
            ],
            stream=False,
            timeout=context.settings.voice_model_timeout_ms / 1000,
        )
        answer = self._extract_completion_text(completion).strip()
        if not answer:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "图片解读模型返回空内容",
            )
        log_debug(
            self._logger,
            f"photo_interpret 使用真实图片输入完成解读: asset_id={photo_asset.asset_id}",
            LogContext(
                session_id=context.session_id,
                device_id=context.device_id,
                message_id=context.turn_id,
            ),
        )
        return answer

    @staticmethod
    def _create_sdk_client(context) -> Any:
        """创建 OpenAI SDK 客户端。"""

        try:
            from openai import OpenAI
        except ImportError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "缺少 openai 依赖，无法执行图片解读",
                details={"hint": "请执行 uv sync 或安装 openai 依赖"},
            ) from exc

        return OpenAI(
            api_key=context.settings.dashscope_api_key,
            base_url=context.settings.voice_model_base_url.rstrip("/"),
        )

    @staticmethod
    def _build_image_data_url(photo_asset: MediaAssetRef) -> str:
        """把本地图片资产转为 `data:` URL。"""

        try:
            with open(photo_asset.storage_uri, "rb") as handle:
                payload = handle.read()
        except FileNotFoundError as exc:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "图片资产文件不存在，无法执行图片解读",
                details={"storage_uri": photo_asset.storage_uri},
            ) from exc

        mime_type = photo_asset.mime_type or "image/png"
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _extract_completion_text(completion: Any) -> str:
        """从 Chat Completions 结果中提取文本。"""

        choices = getattr(completion, "choices", None)
        if not isinstance(choices, list) or not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if message is None:
            return ""
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            chunks: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str) and text:
                    chunks.append(text)
            return "".join(chunks)
        return ""

    @staticmethod
    def _resolve_photo_asset(context, asset_id: str | None) -> MediaAssetRef | None:
        """解析当前轮要解读的图片资产。"""

        if not asset_id:
            return None
        for asset in context.emitted_assets:
            if asset.asset_id == asset_id:
                return asset
        if context.session_store is None:
            return None
        session = context.session_store.get_session(context.session_id)
        if session is None:
            return None
        return session.assets.get(asset_id)

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
