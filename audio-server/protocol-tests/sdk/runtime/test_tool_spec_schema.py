from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig, BaseTool, ToolContext, ToolResult, ToolSpec


class WeatherInput(BaseModel):
    """天气查询输入。"""

    city: str = Field(description="要查询天气的城市名称，例如上海。")
    days: int = Field(default=1, ge=1, le=7, description="要查询的天数，范围 1 到 7。")


class WeatherOutput(BaseModel):
    """天气查询输出。"""

    city: str
    days: int


class WeatherSpecTool(BaseTool):
    """使用 ToolSpec 和 Pydantic 入参的开发者工具样板。"""

    spec = ToolSpec(
        name="weather_spec",
        description="查询指定城市天气。",
        input_model=WeatherInput,
        output_model=WeatherOutput,
        progress_message=("正在查询天气", "我查一下天气"),
        tags=["weather"],
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """返回校验后的入参。"""

        return ToolResult.success({"city": input_data["city"], "days": input_data["days"]})


def test_tool_spec_pydantic_model_generates_provider_schema_and_validates_input(tmp_path) -> None:
    """测试目标：验证开发者可通过 ToolSpec + Pydantic 声明模型可见参数。

    测试方法：注册一个带 Field 描述和取值范围的 Tool，读取 provider schema 并调用。
    预期结果：schema 中包含字段说明、必填项和范围约束；调用时完成默认值填充。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    app.tool_registry.register(WeatherSpecTool())

    schema = next(item for item in app.tool_gateway.provider_schemas() if item["function"]["name"] == "weather_spec")
    parameters = schema["function"]["parameters"]

    assert parameters["properties"]["city"]["description"] == "要查询天气的城市名称，例如上海。"
    assert parameters["properties"]["days"]["minimum"] == 1
    assert parameters["properties"]["days"]["maximum"] == 7
    assert "city" in parameters["required"]

    result = asyncio.run(
        app.tool_gateway.call(
            name="weather_spec",
            user_id="user-tool-spec",
            session_id="sess-tool-spec",
            input_data={"city": "上海"},
        )
    )

    assert result.ok is True
    assert result.data == {"city": "上海", "days": 1}


def test_tool_spec_validation_error_returns_structured_tool_result(tmp_path) -> None:
    """测试目标：验证 Tool 入参错误不会进入业务 run。

    测试方法：传入超出 Pydantic 约束的 days。
    预期结果：ToolGateway 返回 invalid_argument 错误，错误详情包含字段校验信息。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    app.tool_registry.register(WeatherSpecTool())

    result = asyncio.run(
        app.tool_gateway.call(
            name="weather_spec",
            user_id="user-tool-spec",
            session_id="sess-tool-spec",
            input_data={"city": "上海", "days": 99},
        )
    )

    assert result.ok is False
    assert result.error is not None
    assert result.error["code"] == "invalid_argument"
    assert result.error["details"]["errors"][0]["loc"] == ("days",)
