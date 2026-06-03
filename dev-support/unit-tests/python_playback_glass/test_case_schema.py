from pathlib import Path

from realtime_agent_python_playback_glass.case_schema import load_case, load_suite


ROOT = Path(__file__).resolve().parents[3]
DEVICE_ROOT = ROOT / "dev-support/devices/python-playback-glass"


def test_case_schema_loads_smoke_case_with_default_device_fields() -> None:
    """测试目标：验证 smoke Case 能被端侧 schema 加载。

    测试方法：读取 `who_are_you.yaml`，检查默认设备字段、音频输入和断言。
    预期结果：Case 不依赖 server SDK 内部对象即可解析。
    """

    case = load_case(DEVICE_ROOT / "cases/smoke/who_are_you.yaml")

    assert case.id == "who_are_you"
    assert case.device["client_type"] == "python-playback-glass"
    assert case.device["properties"]["realtime_agent.audio_input"] == "sensor.mic"
    assert case.inputs["audio"]["path"].endswith("你是谁呀.wav")
    assert "actuator.speaker" in case.expect["streams"]["includes"]


def test_suite_schema_resolves_relative_case_paths() -> None:
    """测试目标：验证 suite 中的相对 Case 路径可解析。

    测试方法：加载 smoke suite，检查路径数量和存在性。
    预期结果：suite 可作为 CLI 批量运行入口。
    """

    suite = load_suite(DEVICE_ROOT / "suites/smoke.yaml")

    assert suite.id == "smoke"
    assert len(suite.cases) == 2
    assert all(path.exists() for path in suite.cases)
