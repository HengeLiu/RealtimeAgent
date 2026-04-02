"""接入层逻辑测试。"""

from nextgen.apps.glass.gateway.glass_gateway import GlassGateway
from nextgen.apps.phone.gateway.phone_gateway import PhoneGateway


def test_glass_gateway_can_manage_peer_sessions() -> None:
    """验证眼镜接入层可以管理点对点会话。"""

    gateway = GlassGateway()
    gateway.connect()
    session = gateway.open_peer_session("tasksess_peer_001", "phone-001")

    assert session["status"] == "open"
    assert gateway.list_peer_sessions()[0]["peer_device_id"] == "phone-001"


def test_phone_gateway_can_buffer_messages() -> None:
    """验证手机接入层可以缓存收发消息。"""

    gateway = PhoneGateway()
    gateway.connect()
    gateway.send({"message_type": "hint", "text": "请向左"})
    gateway.push_incoming_message({"message_type": "command", "name": "start"})

    assert gateway.outbox[0]["text"] == "请向左"
    assert gateway.receive()["name"] == "start"
