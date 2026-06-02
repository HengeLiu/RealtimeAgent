from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, Field

from realtime_agent import RealtimeAgentApp, RealtimeAgentConfig, BaseTool, ToolContext, ToolError, ToolResult, ToolSpec
from realtime_agent.tools import CloseAudioSessionTool, TOOL_DEFAULT_TIMEOUT_SECONDS, TOOL_MAX_TIMEOUT_SECONDS


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


class OverlongTool(BaseTool):
    """测试用超长 Tool。"""

    spec = ToolSpec(
        name="overlong_tool",
        description="错误示例：超过短生命周期 Tool 超时上限。",
        timeout_seconds=TOOL_MAX_TIMEOUT_SECONDS + 1,
    )

    async def run(self, context: ToolContext, input_data: dict) -> ToolResult:
        """测试不会实际运行。"""

        return ToolResult.success({})


class FakeOutput:
    """测试用输出门面，记录 close_audio_session 是否被调用。"""

    def __init__(self) -> None:
        self.close_calls: list[dict] = []

    async def close_audio_session(self, *, reason: str = "model_requested", close_mode: str = "close_now") -> None:
        """记录关闭请求参数。"""

        self.close_calls.append({"reason": reason, "close_mode": close_mode})


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


def test_plain_tool_without_timeout_uses_short_action_default(tmp_path) -> None:
    """测试目标：验证普通 Tool 未声明超时时由架构层使用 10 秒默认值。

    测试方法：注册未声明 timeout_seconds 的 Tool，读取 ToolGateway 内部 schema。
    预期结果：执行层保留短生命周期默认超时，不需要每个 Tool 重复声明。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))
    app.tool_registry.register(WeatherSpecTool())

    schema = next(item for item in app.tool_gateway.schemas() if item["name"] == "weather_spec")

    assert schema["timeout_seconds"] == TOOL_DEFAULT_TIMEOUT_SECONDS
    assert TOOL_DEFAULT_TIMEOUT_SECONDS == 10.0


def test_tool_registry_rejects_timeout_over_short_action_limit(tmp_path) -> None:
    """测试目标：验证普通 Tool 不能声明超过 10 秒的运行超时。

    测试方法：注册 timeout_seconds 超过上限的 Tool。
    预期结果：注册阶段抛出 ToolError，提示开发者应改用 Task。
    """

    app = RealtimeAgentApp(RealtimeAgentConfig(runs_root=str(tmp_path / "runs")))

    with pytest.raises(ToolError, match="max short-action timeout"):
        app.tool_registry.register(OverlongTool())


def test_close_audio_session_rejects_default_model_requested_without_user_phrase() -> None:
    """测试目标：防止模型把普通插话误判为关闭连续对话。

    测试方法：直接调用 close_audio_session Tool，只传默认 reason，不提供用户明确关闭短语。
    预期结果：Tool 返回 invalid_argument，且不会调用 output.close_audio_session。
    """

    output = FakeOutput()
    tool = CloseAudioSessionTool()
    context = ToolContext(user_id="user-a", session_id="session-a", devices=None, output=output)

    result = asyncio.run(tool.run(context, {"reason": "model_requested"}))

    assert result.ok is False
    assert result.error["code"] == "invalid_argument"
    assert output.close_calls == []


def test_close_audio_session_accepts_explicit_user_close_phrase() -> None:
    """测试目标：保留用户明确要求结束语音会话时的关闭能力。

    测试方法：直接调用 close_audio_session Tool，并提供用户原话里的“结束对话”短语。
    预期结果：Tool 成功，且 output.close_audio_session 收到 close_now 请求。
    """

    output = FakeOutput()
    tool = CloseAudioSessionTool()
    context = ToolContext(user_id="user-a", session_id="session-a", devices=None, output=output)

    result = asyncio.run(
        tool.run(
            context,
            {
                "reason": "user_requested",
                "user_close_phrase": "结束对话",
            },
        )
    )

    assert result.ok is True
    assert output.close_calls == [{"reason": "user_requested", "close_mode": "close_now"}]
