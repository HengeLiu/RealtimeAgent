from __future__ import annotations

from audio_chat import BaseTool, ToolContext, ToolResult


class FindObjectCaptureTool(BaseTool):
    """找物单帧抓拍 Tool。

    主要功能：
    1. 通过 `UserDeviceContext.capture_photo()` 请求端侧上传一张 `sensor.rgb` 图片。
    2. 返回资产引用和目标名称，供 Agent 或后续视觉任务继续分析。
    3. 不关心具体端侧设备，设备选择交给 capability 和 subscription 匹配。
    """

    name = "find_object_capture"
    description = "请求端侧画面并准备一次找物分析"
    progress_message = "正在获取画面"

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

        object_name = str(input_data.get("object_name") or "目标物").strip()
        asset = context.devices.capture_photo(
            reason="find_object_capture",
            timeout_seconds=float(input_data.get("timeout_seconds") or 2),
            freshness_seconds=float(input_data.get("freshness_seconds") or 0),
            configure_payload={"format": "jpeg", "object_name": object_name},
        )
        if asset is None:
            return ToolResult.success(
                data={"captured": False, "found": False, "object_name": object_name},
                message="未收到端侧画面，无法完成找物分析",
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

    name = "start_find_object"
    description = "启动持续 RGB 找物任务"
    progress_message = "正在启动找物任务"

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
