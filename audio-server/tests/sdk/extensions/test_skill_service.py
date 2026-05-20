from __future__ import annotations

import asyncio

import pytest

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.errors import ErrorCode
from realtime_agent.skills import SkillError, SkillService


def _write_skill(root, name: str, body: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_skill_service_reads_only_configured_roots(tmp_path) -> None:
    """测试目标：验证 Skill Service 只读取配置 roots 下的技能。

    测试方法：在 root 下创建 SKILL.md，并尝试读取合法名称和路径穿越名称。
    预期结果：合法 Skill 可读取，非法名称返回结构化参数错误。
    """

    root = tmp_path / "skills"
    _write_skill(
        root,
        "navigation-guide",
        """---
name: navigation-guide
description: 导航说明
tool_allowlist:
  - read_skill
prompt_snippets:
  - 优先确认目的地
---
# 导航说明
只使用公开 Tool。
""",
    )
    service = SkillService(enabled=True, roots=[root])

    document = service.read_skill("navigation-guide")
    assert document.name == "navigation-guide"
    assert document.description == "导航说明"
    assert document.tool_allowlist == ["read_skill"]
    assert "只使用公开 Tool" in document.content

    with pytest.raises(SkillError) as exc:
        service.read_skill("../navigation-guide")
    assert exc.value.code == ErrorCode.INVALID_ARGUMENT


def test_read_skill_tool_returns_structured_error_for_missing_skill(tmp_path) -> None:
    """测试目标：验证 read_skill 读取失败时走 ToolResult 结构化错误。

    测试方法：启用 Skill Service 但不创建目标 Skill，通过 ToolGateway 调用 read_skill。
    预期结果：ToolResult.ok 为 False，错误码为 not_found。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            skill_enabled=True,
            skill_roots=(str(tmp_path / "skills"),),
        )
    )

    result = asyncio.run(
        app.tool_gateway.call(
            name="read_skill",
            user_id="user-skill",
            session_id="session-skill",
            input_data={"name": "missing"},
        )
    )

    assert result.ok is False
    assert result.error["code"] == "not_found"


def test_skill_tool_allowlist_filters_provider_schema_without_bypassing_policy(tmp_path) -> None:
    """测试目标：验证 Skill 能收窄工具白名单但不能绕过 ToolPolicy。

    测试方法：Skill 白名单只允许 read_skill，同时 ToolPolicy deny read_skill。
    预期结果：provider schema 中既没有非白名单工具，也没有被 deny 的 read_skill。
    """

    root = tmp_path / "skills"
    _write_skill(
        root,
        "restricted",
        """---
name: restricted
tool_allowlist:
  - read_skill
---
只能读 Skill。
""",
    )
    app = RealtimeAgentApp(
        RealtimeAgentConfig(
            runs_root=str(tmp_path / "runs"),
            skill_enabled=True,
            skill_roots=(str(root),),
            tools_denylist=("read_skill",),
        )
    )

    assert app.tool_gateway.provider_schemas() == []
