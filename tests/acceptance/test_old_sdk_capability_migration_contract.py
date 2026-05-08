from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
APP_ROOT = ROOT / "app-examples" / "for-blind-app"


def test_for_blind_migration_sample_has_required_structure() -> None:
    """测试目标：确认 F 线迁移样板目录完整。

    测试方法：检查计划要求的五类能力、host、配置、MCP mock 和 testdata 入口。
    预期结果：开发者可以按目录复制或扩展，不需要重新猜 app-root 结构。
    """

    required = [
        APP_ROOT / "README.md",
        APP_ROOT / "server.yaml",
        APP_ROOT / "config/mcp.yaml",
        APP_ROOT / "host/server/main.py",
        APP_ROOT / "host/glass-playback/old-sdk-parity-capabilities.yaml",
        ROOT / "testdata/for-blind/README.md",
    ]
    for capability in ["find_object", "traffic_light", "navigation", "search", "timer"]:
        required.append(APP_ROOT / "capabilities" / capability / "README.md")
        required.append(APP_ROOT / "capabilities" / capability / "__init__.py")
    missing = [str(path.relative_to(ROOT)) for path in required if not path.exists()]

    assert missing == []


def test_for_blind_migration_readme_marks_success_paths_as_backed_by_lane() -> None:
    """测试目标：确认文档口径和 F 线实现同步。

    测试方法：读取 for-blind README 和老 SDK 对齐计划，检查已落地 lane、能力名称和
    设备通讯约束。
    预期结果：文档不再把五类能力写成未来占位，同时仍强调 event + stream 边界。
    """

    readme = (APP_ROOT / "README.md").read_text(encoding="utf-8")
    plan = (ROOT / "docs/old-sdk-parity-development-plan.md").read_text(encoding="utf-8")
    for expected in [
        "old-sdk-parity-capabilities",
        "find_object",
        "traffic_light",
        "navigation",
        "search",
        "timer",
        "event + stream",
        "UserDeviceContext",
    ]:
        assert expected in readme
    assert "uv run python scripts/acceptance_check.py old-sdk-parity-capabilities" in plan
