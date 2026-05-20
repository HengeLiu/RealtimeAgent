from __future__ import annotations

import inspect

from realtime_agent.mcp import McpGateway
from realtime_agent.memory import MemoryService
from realtime_agent.skills import SkillService
from realtime_agent.tools import ToolContext, ToolDeviceFacade


def test_memory_skill_mcp_services_do_not_accept_user_device_context() -> None:
    """测试目标：冻结 Memory / Skill / MCP 不能直接持有设备上下文的契约。

    测试方法：检查三个服务构造函数和公开方法签名。
    预期结果：签名中不出现 `ToolDeviceFacade`，也没有 devices/context 注入参数。
    """

    forbidden_names = {"devices", "device_context", "user_device_context", "context"}
    for cls in (MemoryService, SkillService, McpGateway):
        for name, member in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue
            signature = inspect.signature(member)
            assert forbidden_names.isdisjoint(signature.parameters), f"{cls.__name__}.{name} leaks device context"
            assert "ToolDeviceFacade" not in str(signature)


def test_tool_context_is_the_only_bridge_to_device_capabilities() -> None:
    """测试目标：确认设备能力只通过普通 Tool/Task 上下文间接使用。

    测试方法：检查 ToolContext 字段和服务对象属性。
    预期结果：ToolContext 同时注入 devices 与 C 线服务，但 C 线服务自身没有设备属性。
    """

    annotations = ToolContext.__annotations__
    assert "ToolDeviceFacade" in str(annotations["devices"])
    assert "memory" in annotations
    assert "skills" in annotations
    assert "mcp" in annotations

    for service in (MemoryService(enabled=True), SkillService(enabled=True), McpGateway(enabled=True)):
        assert not hasattr(service, "devices")
        assert not hasattr(service, "device_context")
        assert not hasattr(service, "user_device_context")
        assert not isinstance(service, ToolDeviceFacade)
