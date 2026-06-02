from pathlib import Path

import yaml

from realtime_agent_python_playback_glass.conversation_regression import (
    ConversationRegressionTarget,
    DEFAULT_TARGETS,
    port_is_free,
    write_conversation_server_config,
)


ROOT = Path(__file__).resolve().parents[4]
BASE_CONFIG = ROOT / "examples/device_app_demo/agent-server/server.yaml"


def test_conversation_regression_derives_omni_manual_config(tmp_path: Path) -> None:
    """测试目标：验证 conversation 回归入口能从正式示例配置派生 Omni Manual server 配置。

    测试方法：调用配置派生函数，读取生成的 YAML 并检查运行时、端口、模式和 VAD 设置。
    预期结果：生成配置启用 conversation runtime、Omni manual turn detection 和 server-only VAD。
    """

    target = next(item for item in DEFAULT_TARGETS if item.name == "omni-manual")
    config_path = write_conversation_server_config(base_config_path=BASE_CONFIG, target=target, target_root=tmp_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert data["agent"]["mode"] == "omni"
    assert data["agent"]["conversation"]["runtime"] == "conversation"
    assert data["agent"]["omni"]["turn_detection"] == "manual"
    assert data["audio_pipeline"]["vad"] == "server_only"
    assert data["server"]["port"] == 9876
    assert data["server"]["public_url"] == "http://127.0.0.1:9876"
    assert data["paths"]["runtime_root"] == str(tmp_path / "runs")


def test_conversation_regression_derives_vl_config(tmp_path: Path) -> None:
    """测试目标：验证 conversation 回归入口能从正式示例配置派生 VL server 配置。

    测试方法：构造 VL 回归目标，读取生成的 YAML 并检查运行时、模式和 ASR/VAD 策略。
    预期结果：生成配置启用 conversation runtime、vision 模式和 provider speech boundary。
    """

    target = ConversationRegressionTarget(name="vl-conversation", agent_mode="vision", port=9877, audio_vad="provider")
    config_path = write_conversation_server_config(base_config_path=BASE_CONFIG, target=target, target_root=tmp_path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))

    assert data["agent"]["mode"] == "vision"
    assert data["agent"]["conversation"]["runtime"] == "conversation"
    assert data["audio_pipeline"]["vad"] == "provider"
    assert data["server"]["port"] == 9877
    assert data["paths"]["runtime_root"] == str(tmp_path / "runs")


def test_port_is_free_reports_unused_high_port() -> None:
    """测试目标：验证回归入口可以检查本地端口可用性。

    测试方法：检查一个通常未占用的高端口。
    预期结果：函数返回布尔值，供回归入口在启动 server 前做保护。
    """

    assert isinstance(port_is_free(43210), bool)
