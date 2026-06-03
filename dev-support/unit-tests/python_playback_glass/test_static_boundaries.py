from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
DEVICE_ROOT = ROOT / "dev-support/devices/python-playback-glass"


def test_python_playback_glass_does_not_import_server_internal_runtime() -> None:
    """测试目标：验证回放端侧没有依赖 server 内部运行时对象。

    测试方法：扫描端侧源码，检查禁止导入和调用的内部类/服务名。
    预期结果：端侧只通过协议对话，不实例化 `RealtimeAgentApp` 等 server 对象。
    """

    source = "\n".join(path.read_text(encoding="utf-8") for path in (DEVICE_ROOT / "realtime_agent_python_playback_glass").glob("*.py"))

    forbidden = [
        "RealtimeAgentApp",
        "RealtimeAgentConfig",
        "ToolGateway",
        "TaskEngine",
        "OutputService",
        "AssetService",
        "register_device(",
        "publish_control_event(",
        "open_input_stream(",
        "write_input_chunk(",
        "stream_service",
    ]
    for item in forbidden:
        assert item not in source


def test_pytest_integration_keeps_external_cli_boundary() -> None:
    """测试目标：验证 pytest 集成文档和测试入口保留外层 CLI 边界。

    测试方法：检查 README 中推荐 `python -m realtime_agent_python_playback_glass`。
    预期结果：没有新增 `realtime-agent.system-test` 命令。
    """

    readme = (DEVICE_ROOT / "README.md").read_text(encoding="utf-8")
    assert "python -m realtime_agent_python_playback_glass run" in readme
    assert "realtime-agent.system-test" not in readme
