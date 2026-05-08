"""多模态输入能力与图片处理策略。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from agent_core.context import MediaAssetRef


class ImageInputPolicy(StrEnum):
    """图片输入策略。

    主要功能：
        描述当前轮图片应直接传给模型、作为后置工具资产，还是完全关闭。
    """

    DIRECT_WHEN_SUPPORTED = "direct_when_supported"
    TOOL_WHEN_NEEDED = "tool_when_needed"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class ModelCapability:
    """模型模态能力描述。

    主要功能：
        把模型能否接收文本、音频、图片和输出文本、音频显式化，避免执行逻辑绑死在模型名上。

    主要属性：
        model_name: 模型名称。
        supports_text_input: 是否支持文本输入。
        supports_audio_input: 是否支持音频输入。
        supports_image_input: 是否支持图片输入。
        supports_text_output: 是否支持文本输出。
        supports_audio_output: 是否支持音频输出。
        supports_streaming: 是否支持流式输出。
        supports_tool_calling: 是否支持工具调用。
    """

    model_name: str
    supports_text_input: bool = True
    supports_audio_input: bool = False
    supports_image_input: bool = False
    supports_text_output: bool = True
    supports_audio_output: bool = False
    supports_streaming: bool = True
    supports_tool_calling: bool = True

    @classmethod
    def from_model_name(cls, model_name: str) -> "ModelCapability":
        """根据模型名生成默认能力描述。

        参数：
            model_name: 当前要调用的模型名称。

        返回值：
            推断出的模型能力。当前只做保守推断，后续可由配置覆盖。

        异常情况：
            本方法不抛出业务异常。
        """

        normalized = model_name.lower()
        is_omni = "omni" in normalized
        looks_visual = is_omni or "vl" in normalized or "vision" in normalized
        return cls(
            model_name=model_name,
            supports_audio_input=is_omni,
            supports_image_input=looks_visual,
            supports_audio_output="realtime" in normalized or is_omni,
        )


@dataclass(slots=True)
class ImageInputPlan:
    """当前轮图片输入规划结果。"""

    direct_assets: list[MediaAssetRef]
    deferred_assets: list[MediaAssetRef]
    policy: ImageInputPolicy
    model_capability: ModelCapability


class AgentInputPlanner:
    """根据模型能力和策略规划当前轮图片资产。

    主要功能：
        1. 将图片直传和后置工具两种路径从隐式 if 判断中抽出。
        2. 为当前默认 Omni 链路选择图片直传。
        3. 为不支持图片的模型预留 deferred assets。
    """

    def __init__(
        self,
        *,
        model_capability: ModelCapability,
        image_policy: ImageInputPolicy = ImageInputPolicy.DIRECT_WHEN_SUPPORTED,
    ) -> None:
        self._model_capability = model_capability
        self._image_policy = image_policy

    def plan_images(self, image_assets: list[MediaAssetRef]) -> ImageInputPlan:
        """规划图片资产去向。

        参数：
            image_assets: 当前轮可用图片资产。

        返回值：
            图片直传和后置处理的拆分结果。

        异常情况：
            本方法只处理内存对象，不主动抛出业务异常。
        """

        if self._image_policy == ImageInputPolicy.DISABLED:
            return ImageInputPlan(
                direct_assets=[],
                deferred_assets=[],
                policy=self._image_policy,
                model_capability=self._model_capability,
            )
        if self._image_policy == ImageInputPolicy.DIRECT_WHEN_SUPPORTED and self._model_capability.supports_image_input:
            return ImageInputPlan(
                direct_assets=list(image_assets),
                deferred_assets=[],
                policy=self._image_policy,
                model_capability=self._model_capability,
            )
        return ImageInputPlan(
            direct_assets=[],
            deferred_assets=list(image_assets),
            policy=self._image_policy,
            model_capability=self._model_capability,
        )
