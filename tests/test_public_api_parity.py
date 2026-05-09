from __future__ import annotations

import inspect

import audio_chat


def test_public_device_facade_api_is_importable_from_top_level() -> None:
    """测试目标：冻结设备 Facade 架构阶段的顶层公开 API。

    测试方法：逐个从 `audio_chat` 顶层读取开发者应依赖的类型名。
    预期结果：业务代码无需 import SDK 内部 service 模块即可获得 Tool、Task、
    Context、设备 Facade、资产引用和 trace 对象。
    """

    expected = {
        "BaseTool",
        "ToolContext",
        "ToolResult",
        "BaseTask",
        "TaskContext",
        "TaskEvent",
        "TaskRef",
        "ToolDeviceFacade",
        "ArtifactRef",
        "CapabilityTrace",
    }

    assert expected <= set(audio_chat.__all__)
    for name in expected:
        assert getattr(audio_chat, name) is not None


def test_public_api_points_to_developer_safe_modules() -> None:
    """测试目标：确认顶层公开对象不会要求开发者感知底层服务实现。

    测试方法：检查关键对象所属模块和构造签名。
    预期结果：`ToolDeviceFacade 和 TaskDeviceFacade 来自公开 context 门面；Tool / Task 上下文只暴露
    `devices`，不暴露 ControlService、StreamService、AssetService 或 OutputService。
    """

    assert audio_chat.ToolDeviceFacade.__module__ in {
        "audio_chat.tools",
        "audio_chat.context",
    }
    assert audio_chat.CapabilityTrace is audio_chat.ToolTrace

    tool_annotations = audio_chat.ToolContext.__annotations__
    task_annotations = audio_chat.TaskContext.__annotations__
    assert "devices" in tool_annotations
    assert "devices" in task_annotations
    for cls in (audio_chat.ToolContext, audio_chat.TaskContext):
        signature_text = str(inspect.signature(cls))
        for forbidden in ("ControlService", "StreamService", "AssetService", "OutputService"):
            assert forbidden not in signature_text
