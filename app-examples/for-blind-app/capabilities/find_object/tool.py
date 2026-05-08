from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class FindObjectCaptureInput(BaseModel):
    """找物抓拍输入参数。"""

    object_name: str = Field(default="目标物", description="用户想要查找的物品名称。")
    freshness_seconds: float = Field(default=0, ge=0, description="允许复用缓存图片的最长秒数。")
    timeout_seconds: float = Field(default=2, gt=0, description="等待图片返回的超时时间，单位秒。")


class FindObjectCaptureOutput(BaseModel):
    """找物抓拍输出结构。"""

    captured: bool = Field(description="是否收到画面。")
    found: bool = Field(description="当前 mock 视觉处理是否认为找到目标。")
    object_name: str = Field(description="要查找的物品名称。")
    asset_id: str | None = Field(default=None, description="图片资产 ID。")
    path: str | None = Field(default=None, description="本地调试路径。")
    source: str | None = Field(default=None, description="结果来源。")


class StartFindObjectInput(BaseModel):
    """持续找物任务输入参数。"""

    object_name: str = Field(default="目标物", description="用户想要持续查找的物品名称。")
    frame_limit: int = Field(default=3, ge=1, le=60, description="本次任务最多分析的图片帧数。")


class StartFindObjectOutput(BaseModel):
    """持续找物任务输出结构。"""

    started: bool = Field(description="是否成功创建任务。")
    task_id: str | None = Field(default=None, description="任务 ID。")
    state: str | None = Field(default=None, description="任务状态。")
    reason: str | None = Field(default=None, description="未启动原因。")


class FindObjectCaptureTool(BaseTool):
    """找物单帧抓拍 Tool。

    主要功能：
    1. 通过 `UserDeviceContext.request_asset("sensor.rgb")` 请求端侧上传一张图片资产。
    2. 返回资产引用和目标名称，供 Agent 或后续视觉任务继续分析。
    3. 不关心具体端侧设备，设备选择交给 event/filter subscription 匹配。
    """

    spec = ToolSpec(
        name="find_object_capture",
        description="当用户想确认眼前是否有某个物品时调用。只做一次画面检查；需要持续寻找时使用 start_find_object。",
        input_model=FindObjectCaptureInput,
        output_model=FindObjectCaptureOutput,
        progress_message=("我先看一下有没有这个东西。", "稍等，我看一下前面。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行找物抓拍。

        主要逻辑：
        1. 从模型参数读取 `object_name`。
        2. 通过公开设备上下文请求 RGB 图片资产。
        3. 返回资产 ID、路径和一个本地 mock 识别摘要。

        参数：
        1. `context`：SDK 注入的 Tool 上下文。
        2. `input_data`：工具参数，可包含 `object_name`、`timeout_seconds`。

        返回值：
        1. 成功时包含 `found`、`object_name` 和 `asset_id`。
        2. 超时时返回 `captured=false`。

        异常情况：
        1. 事件发布、stream 或资产缓存异常由 ToolGateway 转成失败结果。
        """

        object_name = input_data["object_name"].strip()
        asset = context.devices.request_asset(
            "sensor.rgb",
            freshness_seconds=float(input_data["freshness_seconds"]),
            configure_payload={
                "mode": "single",
                "reason": "find_object_capture",
                "format": "jpeg",
                "object_name": object_name,
            },
            timeout_seconds=float(input_data["timeout_seconds"]),
        )
        if asset is None:
            return ToolResult.success(
                data={"captured": False, "found": False, "object_name": object_name},
                message="未收到画面，无法完成找物分析",
            )
        return ToolResult.success(
            data={
                "captured": True,
                "found": True,
                "object_name": object_name,
                "asset_id": asset.asset_id,
                "path": asset.path,
                "source": "mock_vision",
            },
            message=f"已收到画面，mock 识别认为{object_name}在前方",
            assets=[asset],
        )


class StartFindObjectTaskTool(BaseTool):
    """启动持续找物视觉任务的 Tool。"""

    spec = ToolSpec(
        name="start_find_object",
        description="当用户要求持续寻找某个物品、边走边找或需要持续引导时调用。",
        input_model=StartFindObjectInput,
        output_model=StartFindObjectOutput,
        progress_message=("好的，我开始帮你找。", "我来持续找一下。"),
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """创建找物视觉 Task。

        主要逻辑：通过 TaskEngine 创建 `find_object_vision_task`，不直接控制手机或眼镜。
        参数：`input_data` 可包含 `object_name`、`frame_limit`。
        返回值：包含任务引用。
        异常情况：TaskEngine 未配置或任务创建失败时返回结构化失败。
        """

        if context.tasks is None:
            return ToolResult.success(data={"started": False, "reason": "task_engine_unavailable"})
        ref = await context.tasks.create(
            task_type="find_object_vision_task",
            user_id=context.user_id,
            session_id=context.session_id,
            input_data=dict(input_data),
            summary="持续找物任务",
        )
        return ToolResult.success(
            data={"started": True, "task_id": ref.task_id, "state": ref.state},
            tasks=[ref],
            message="找物任务已启动",
        )
