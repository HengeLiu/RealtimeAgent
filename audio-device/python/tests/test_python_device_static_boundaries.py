from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_device_sdk_does_not_import_server_runtime_objects() -> None:
    """测试目标：确认 Python 端侧 SDK 不依赖 server 内部运行时对象。

    测试方法：扫描 SDK 源码，检查禁止出现的 server 内部类和服务名。
    预期结果：SDK 只实现端侧通讯，不实例化 `AudioChatApp`、`TaskEngine` 等对象。
    """

    source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "src/audio_chat_device").glob("*.py"))
    forbidden = [
        "AudioChatApp",
        "AudioChatConfig",
        "ToolGateway",
        "TaskEngine",
        "OutputService",
        "AssetService",
        "register_device(",
        "publish_control_event(",
        "stream_service",
    ]
    for item in forbidden:
        assert item not in source
