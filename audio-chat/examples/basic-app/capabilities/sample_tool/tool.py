from __future__ import annotations

from audio_chat import BaseTool, ToolContext, ToolResult


class EchoTool(BaseTool):
    """最小 Tool 样板。

    主要功能：演示业务 Tool 通过 SDK 注入的 `ToolContext` 工作。
    主要方法：`run()` 返回输入文本和当前用户设备数量。
    主要属性：`name` 是自动发现和 Agent 调用使用的稳定工具名。
    """

    name = "echo_text"
    description = "返回输入文本，并附带当前用户在线设备数量。"

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """执行 echo Tool。

        主要逻辑：
        1. 从输入中读取 text。
        2. 通过 `context.devices.get_devices()` 读取只读设备快照。
        3. 返回结构化 `ToolResult`。

        参数：`context` 为 SDK 注入上下文，`input_data` 为模型传入参数。
        返回值：成功 `ToolResult`。
        异常情况：本样板不主动抛出异常。
        """

        text = str(input_data.get("text") or "")
        return ToolResult.success(
            data={"text": text, "device_count": len(context.devices.get_devices())},
            message=text,
        )

