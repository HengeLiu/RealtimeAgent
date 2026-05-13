from __future__ import annotations

import asyncio
import json

from audio_chat.protocol import Event
from audio_chat_python_phone_mock.phone_mock import NetworkPythonPhoneMockEndpoint


class FakeControlWebSocket:
    """测试用控制 WebSocket。

    主要功能：收集 phone 端通过 `send_str()` 发出的 JSON 控制事件。
    主要属性：`messages` 保存原始 JSON 文本，便于按协议解析校验。
    """

    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send_str(self, message: str) -> None:
        """记录一条待发送控制消息。"""

        self.messages.append(message)


def test_python_phone_sends_protocol_heartbeat_event() -> None:
    """测试目标：验证 python-phone 长驻端能发送标准设备心跳。

    测试方法：构造 phone endpoint 和 fake 控制 WebSocket，直接调用一次心跳发送方法。
    预期结果：发出的事件为 `control.device.heartbeat.received`，producer/session 均为 phone 设备。
    """

    endpoint = NetworkPythonPhoneMockEndpoint(
        server_url="http://127.0.0.1:8765",
        user_id="user-browser-glass-001",
        device_id="dev-python-phone-preview",
        device_name="Python 手机视频显示端",
        properties={"device_role": "phone", "peer.video.receiver": True},
        supports={"sensors": [], "actuators": []},
    )
    ws = FakeControlWebSocket()

    asyncio.run(endpoint._send_heartbeat(ws))

    assert len(ws.messages) == 1
    event = Event.from_dict(json.loads(ws.messages[0]))
    assert event.event_name == "control.device.heartbeat.received"
    assert event.user_id == "user-browser-glass-001"
    assert event.producer_id == "dev-python-phone-preview"
    assert event.session_id == "dev-python-phone-preview"
    assert event.payload["device_id"] == "dev-python-phone-preview"
