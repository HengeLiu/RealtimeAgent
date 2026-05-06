from __future__ import annotations

from audio_chat.control import ControlService, PublishResult
from audio_chat.protocol import Event


class DeviceCommandService:
    """SDK 内部端侧命令服务。

    主要功能：承接 `DeviceHandle` 已经选择好的设备对象，并把配置 stream、启动任务、
    停止任务等命令转成内部受控定向投递。
    主要方法：`send_to_device()`。
    主要属性：`control_service` 是唯一真实投递协作者；业务代码不能直接使用本服务。
    """

    def __init__(self, *, control_service: ControlService) -> None:
        self.control_service = control_service

    def send_to_device(self, *, device_id: str, event: Event) -> PublishResult:
        """向 SDK 已解析出的设备连接发送命令。

        主要逻辑：只接受 `DeviceHandle` 内部传入的 device_id，并委托 Control Service
        的内部投递方法；事件格式本身仍不携带 target device 字段。
        参数：`device_id` 为 SDK 选择出的设备，`event` 为控制事件。
        返回值：`PublishResult`。
        异常情况：事件非法时由 Control Service 抛出异常。
        """
        return self.control_service._push_to_resolved_device(device_id, event)
