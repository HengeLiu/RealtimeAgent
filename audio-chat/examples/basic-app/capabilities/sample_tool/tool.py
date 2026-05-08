from __future__ import annotations

from pydantic import BaseModel, Field

from audio_chat import BaseTool, ToolContext, ToolResult, ToolSpec


class EchoInput(BaseModel):
    """Echo Tool 输入参数。

    主要功能：声明模型调用 `echo_text` 时必须提供的结构化参数。
    主要属性：`text` 会被转换为 provider function calling schema。
    """

    text: str = Field(description="要原样返回的文本。")


class EchoOutput(BaseModel):
    """Echo Tool 输出结果。

    主要功能：声明 Tool 返回给模型的主要结构，便于开发者理解返回数据边界。
    """

    text: str = Field(description="返回的文本。")
    device_count: int = Field(description="当前用户在线设备数量。")


class EchoTool(BaseTool):
    """最小 Tool 样板。

    主要功能：演示业务 Tool 通过 SDK 注入的 `ToolContext` 工作。
    主要方法：`run()` 返回输入文本和当前用户设备数量。
    主要属性：`name` 是自动发现和 Agent 调用使用的稳定工具名。
    """

    spec = ToolSpec(
        name="echo_text",
        description="返回输入文本，并附带当前用户在线设备数量。",
        input_model=EchoInput,
        output_model=EchoOutput,
    )

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

        text = input_data["text"]
        return ToolResult.success(
            data={"text": text, "device_count": len(context.devices.get_devices())},
            message=text,
        )
