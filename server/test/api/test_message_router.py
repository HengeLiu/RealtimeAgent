from api.router import MessageRouter
from protocol.enums import MessageType
from protocol.messages.envelope import Endpoint, Envelope



def _envelope(message_name: str, target_module: str) -> Envelope:
    return Envelope(
        message_id="msg_1",
        trace_id="trace_1",
        message_type=MessageType.COMMAND,
        message_name=message_name,
        protocol_version="1.0.0",
        source=Endpoint(device_id="dev_glass_001", module="glass-api"),
        target=Endpoint(device_id="dev_server_main", module=target_module),
        timestamp="2026-04-07T22:00:00+08:00",
        payload={},
    )



def test_message_router_routes_by_module_then_domain_fallback() -> None:
    router = MessageRouter()
    called = []

    def module_handler(_: Envelope) -> list[Envelope]:
        called.append("module")
        return []

    def domain_handler(_: Envelope) -> list[Envelope]:
        called.append("domain")
        return []

    router.register_module("server-api", module_handler)
    router.register_domain("peer", domain_handler)

    router.route(_envelope("peer.prepare_link", "server-api"))
    assert called == ["module", "domain"]
