from __future__ import annotations

import inspect

import realtime_agent


def test_public_device_facade_api_is_importable_from_top_level() -> None:
    """测试目标：冻结设备 Facade 架构阶段的顶层公开 API。

    测试方法：逐个从 `realtime_agent` 顶层读取开发者应依赖的类型名。
    预期结果：业务代码无需 import SDK 内部 service 模块即可获得 Tool、Context、
    设备 Facade、资产引用和 trace 对象。Task 概念已统一并入 Tool。
    """

    expected = {
        "BaseTool",
        "ToolContext",
        "ToolResult",
        "ToolDeviceFacade",
        "BackgroundDeviceFacade",
        "ArtifactRef",
        "CapabilityTrace",
    }

    assert expected <= set(realtime_agent.__all__)
    for name in expected:
        assert getattr(realtime_agent, name) is not None


def test_public_api_points_to_developer_safe_modules() -> None:
    """测试目标：确认顶层公开对象不会要求开发者感知底层服务实现。

    测试方法：检查关键对象所属模块和构造签名。
    预期结果：`ToolDeviceFacade` 和 `BackgroundDeviceFacade` 来自公开 context 门面；
    Tool 上下文只暴露 `devices`，不暴露 ControlService、StreamService、AssetService 或 OutputService。
    """

    assert realtime_agent.ToolDeviceFacade.__module__ in {
        "realtime_agent.tools",
        "realtime_agent.context",
    }
    assert realtime_agent.CapabilityTrace is realtime_agent.ToolTrace

    tool_annotations = realtime_agent.ToolContext.__annotations__
    assert "devices" in tool_annotations
    signature_text = str(inspect.signature(realtime_agent.ToolContext))
    for forbidden in ("ControlService", "StreamService", "AssetService", "OutputService"):
        assert forbidden not in signature_text
