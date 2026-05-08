"""找物体 Tool 示例。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from openaiglasses import BaseTool, CapabilityResult


class StartFindObjectInput(BaseModel):
    """找物体 Tool 输入。"""

    target_object: str = Field(description="用户想寻找的具体物品名称，例如“钥匙”“手机”“水杯”。")
    model_name: str | None = Field(default=None, description="可选的手机端 CoreML YOLO 模型资源名，不带扩展名。")
    score_threshold: float | None = Field(default=None, description="可选的手机端检测置信度阈值。")
    frame_stride: int | None = Field(default=None, description="可选的手机端推理抽帧间隔，1 表示每帧处理。")


class StartFindObjectOutput(BaseModel):
    """找物体 Tool 输出。"""

    task_id: str
    task_type: str
    state: str
    target_object: str
    task_data: dict[str, Any]


class StartFindObjectTool(BaseTool):
    """启动找物体任务的工具。

    主要功能：
    1. 接收目标物体名称。
    2. 通过 SDK 任务上下文创建找物体任务。
    3. 返回结构化任务信息。

    主要方法：
    1. `run`：执行工具逻辑。
    """

    name = "start_find_object"
    description = (
        "当用户要求帮忙寻找某个具体物品时调用，会启动持续找物后台任务。"
        "如果只是询问当前画面里有什么，优先使用拍照和视觉理解，不要启动找物任务。"
    )
    input_model = StartFindObjectInput
    output_model = StartFindObjectOutput

    def run(self, context, input_data: dict[str, Any]) -> CapabilityResult:
        """启动找物体任务。

        参数：
        1. `context`：SDK 设备组上下文。
        2. `input_data`：业务输入，包含 `target_object`。

        返回值：
        1. `CapabilityResult`：任务创建结果。

        异常情况：
        1. 目标物体为空时返回失败结果。
        """

        target_object = str(input_data.get("target_object") or "").strip()
        if not target_object:
            return CapabilityResult.failed(code="invalid_input", message="target_object 不能为空")
        task_input: dict[str, Any] = {"target_object": target_object}
        model_name = str(input_data.get("model_name") or "").strip()
        if model_name:
            task_input["model_name"] = model_name
        if input_data.get("score_threshold") is not None:
            task_input["score_threshold"] = float(input_data["score_threshold"])
        if input_data.get("frame_stride") is not None:
            task_input["frame_stride"] = max(1, int(input_data["frame_stride"]))

        task_runtime = context.create_task(
            task_type="find_object_task",
            input_data=task_input,
        )
        return CapabilityResult.success(
            data={
                "task_id": task_runtime.task_id,
                "task_type": task_runtime.task_type,
                "state": task_runtime.state,
                "target_object": target_object,
                "task_data": task_runtime.data,
            },
            message=f"已准备启动找物体任务：{target_object}",
        )
