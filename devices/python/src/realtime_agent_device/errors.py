class RealtimeAgentDeviceError(Exception):
    """端侧 SDK 基础异常。"""


class RegistrationFailedError(RealtimeAgentDeviceError):
    """设备注册被 server 拒绝。"""


class ProtocolError(RealtimeAgentDeviceError):
    """收到不符合协议预期的消息。"""
