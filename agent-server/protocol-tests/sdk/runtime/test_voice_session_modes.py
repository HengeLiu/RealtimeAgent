from __future__ import annotations

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig
from realtime_agent.conversation.core.omni import OmniManualConversationRuntime


def test_agent_mode_realtime_alias_builds_realtime_core(tmp_path) -> None:
    """测试目标：验证 `agent.mode=realtime` 进入正式 Omni Manual 链路。

    测试方法：直接用 `RealtimeAgentConfig(agent_mode="realtime")` 创建 App。
    预期结果：实际构建 `OmniManualConversationRuntime`，且不在构造阶段连接真实 provider。
    """

    app = RealtimeAgentApp(
        RealtimeAgentConfig(runs_root=str(tmp_path / "runs"), agent_mode="realtime", omni_provider="mock")
    )

    assert isinstance(app.agent_core, OmniManualConversationRuntime)


def test_agent_mode_from_yaml_selects_realtime_core(tmp_path) -> None:
    """测试目标：验证 YAML 通过 `agent.mode` 选择正式 Omni Manual 链路。

    测试方法：创建 app 根目录，在 `server.yaml` 中写入 `agent.mode=realtime`。
    预期结果：构建 `OmniManualConversationRuntime`，不依赖 voice 配置块。
    """

    omni_app_dir = tmp_path / "omni-app"
    omni_app_dir.mkdir()
    omni_config = omni_app_dir / "server.yaml"
    omni_config.write_text(
        "agent:\n  mode: realtime\n  realtime:\n    provider: mock\nobservability:\n  runs_root: runs-omni\n",
        encoding="utf-8",
    )
    omni_app = RealtimeAgentApp(RealtimeAgentConfig.from_yaml(omni_config))

    assert isinstance(omni_app.agent_core, OmniManualConversationRuntime)
