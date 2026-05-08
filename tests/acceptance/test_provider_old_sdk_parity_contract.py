from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_old_sdk_parity_provider_lane_is_registered() -> None:
    """测试目标：确认 H 线真实 provider 与外部服务稳定性有独立验收入口。

    测试方法：动态读取 `scripts/acceptance_check.py` 中的 CHECKS，检查
    `old-sdk-parity-provider` 覆盖 provider、MCP、preflight 和真实 provider 集成测试。
    预期结果：线路存在，且缺真实 key 时集成测试可由 pytest skip 处理，不阻塞 all。
    """

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "acceptance_check.py"
    spec = importlib.util.spec_from_file_location("acceptance_check", script_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    assert "old-sdk-parity-provider" in module.CHECKS
    command_text = "\n".join(" ".join(command.command) for command in module.CHECKS["old-sdk-parity-provider"])
    assert "tests/test_provider_degradation_policy.py" in command_text
    assert "tests/test_mcp_external_server_smoke.py" in command_text
    assert "tests/integration/test_dashscope_providers.py" in command_text
    assert "audio-chat.dev.preflight" in command_text
