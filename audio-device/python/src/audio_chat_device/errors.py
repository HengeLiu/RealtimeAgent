class AudioChatDeviceError(Exception):
    """端侧 SDK 基础异常。"""


class RegistrationFailedError(AudioChatDeviceError):
    """设备注册被 server 拒绝。"""


class ProtocolError(AudioChatDeviceError):
    """收到不符合协议预期的消息。"""
