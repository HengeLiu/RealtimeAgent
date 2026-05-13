"""Python phone mock endpoint package."""

__all__ = [
    "NetworkPythonPhoneMockEndpoint",
    "PhoneTaskHandlerRegistry",
    "run_network_phone_mock",
]


def __getattr__(name: str):
    """按需加载网络 phone mock。

    主要逻辑：避免导入 remote_task / peer_video 等轻量 helper 时立即加载完整网络端点
    依赖，便于端侧 helper 单测独立运行。
    参数：`name` 为要访问的导出名。
    返回值：对应对象。
    异常情况：未知名称抛出 AttributeError。
    """

    if name in __all__:
        from audio_chat_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint, PhoneTaskHandlerRegistry, run_network_phone_mock

        return {
            "NetworkPythonPhoneMockEndpoint": NetworkPythonPhoneMockEndpoint,
            "PhoneTaskHandlerRegistry": PhoneTaskHandlerRegistry,
            "run_network_phone_mock": run_network_phone_mock,
        }[name]
    raise AttributeError(name)
