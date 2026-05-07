from __future__ import annotations

import asyncio
import json

from aiohttp.test_utils import make_mocked_request

from audio_chat.app import AudioChatApp
from audio_chat.control import ControlService, DeviceAuthenticator
from audio_chat.protocol import Event
from audio_chat.server import AudioChatHttpServer


class FakeConnection:
    """设备注册管理测试连接。

    主要功能：模拟控制连接的事件投递和关闭。
    主要方法：`close()` 记录被新连接替换的原因。
    主要属性：`events` 记录下发事件，`closed_reason` 记录关闭原因。
    """

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.events: list[Event] = []
        self.closed_reason = ""

    def push_event(self, event: Event) -> None:
        self.events.append(event)

    def push_stream_chunk(self, chunk: object) -> None:
        pass

    def close(self, *, reason: str) -> None:
        self.closed_reason = reason


def _registration(user_id: str, device_id: str, *, token: str = "token-ok") -> Event:
    """构造 static_token 注册事件。

    主要逻辑：注册同一 device_id 时可切换 user_id，用于验证绑定冲突。
    参数：用户编号、设备编号和静态 token。
    返回值：注册请求事件。
    异常情况：无。
    """

    return Event(
        event_name="control.device.register.requested",
        user_id=user_id,
        producer_id=device_id,
        payload={
            "device_id": device_id,
            "device_name": device_id,
            "client_type": "python-playback",
            "sdk_version": "audio-chat-test",
            "auth": {"mode": "static_token", "token": token},
            "capabilities": {"streams.produce": ["sensor.mic"], "streams.consume": ["actuator.speaker"]},
            "subscriptions": [{"event": "control.audio_session.*"}],
        },
    )


def test_reconnect_replaces_old_connection_and_records_binding_diagnostics() -> None:
    """测试目标：验证同 user 下同 device 重连会覆盖旧连接并写入诊断。

    测试方法：同一用户连续注册同一设备，传入两个不同 FakeConnection。
    预期结果：旧连接收到 replaced 原因，snapshot 标记 replaced_connection。
    """

    service = ControlService(authenticator=DeviceAuthenticator(mode="static_token", device_tokens={"dev-001": "token-ok"}))
    first = FakeConnection("dev-001")
    second = FakeConnection("dev-001")

    first_response = service.register_device(_registration("user-001", "dev-001"), first)
    second_response = service.register_device(_registration("user-001", "dev-001"), second)
    snapshot = service.build_device_snapshot("dev-001")

    assert first_response.event_name == "control.device.registered"
    assert second_response.event_name == "control.device.registered"
    assert first.closed_reason == "replaced_by_new_connection"
    assert snapshot["connection_state"] == "online"
    assert snapshot["binding"]["bound_user_id"] == "user-001"
    assert snapshot["binding"]["replaced_connection"] is True


def test_cross_user_device_claim_is_rejected_without_breaking_existing_device() -> None:
    """测试目标：验证不同 user 抢占同 device 会失败且不破坏已有在线设备。

    测试方法：`user-001` 成功注册后，`user-002` 使用同 device_id 注册。
    预期结果：第二次注册失败，active device set 仍属于 `user-001`。
    """

    service = ControlService(authenticator=DeviceAuthenticator(mode="static_token", device_tokens={"dev-001": "token-ok"}))
    service.register_device(_registration("user-001", "dev-001"), FakeConnection("dev-001"))

    denied = service.register_device(_registration("user-002", "dev-001"), FakeConnection("dev-001"))
    owner_snapshot = service.build_device_snapshot("dev-001")
    denied_user_snapshot = service.build_user_snapshot("user-002")

    assert denied.event_name == "control.device.register.failed"
    assert denied.payload["reason"] == "device_bound_to_other_user"
    assert [device.device_id for device in service.get_active_device_set("user-001").devices] == ["dev-001"]
    assert service.get_active_device_set("user-002").devices == ()
    assert owner_snapshot["connection_state"] == "online"
    assert owner_snapshot["binding"]["last_conflict_user_id"] == "user-002"
    assert denied_user_snapshot["registration_failures"][0]["register_failed_reason"] == "device_bound_to_other_user"


def test_failed_registration_snapshot_keeps_recent_error_without_token_leak() -> None:
    """测试目标：验证注册失败快照包含最近错误和鉴权诊断，但不泄露 token。

    测试方法：使用错误 static token 注册设备后读取 debug snapshot。
    预期结果：snapshot 能解释 invalid_token，字符串化结果不包含原始 token。
    """

    service = ControlService(authenticator=DeviceAuthenticator(mode="static_token", device_tokens={"dev-bad": "token-ok"}))

    failed = service.register_device(_registration("user-001", "dev-bad", token="token-secret-value"), FakeConnection("dev-bad"))
    snapshot = service.build_device_snapshot("dev-bad")

    assert failed.event_name == "control.device.register.failed"
    assert snapshot["register_failed_reason"] == "invalid_token"
    assert snapshot["last_error"]["code"] == "registration_failed"
    assert snapshot["auth"]["status"] == "failed"
    assert snapshot["auth"]["reason"] == "invalid_token"
    assert snapshot["auth"]["token_present"] is True
    assert "token-secret-value" not in str(snapshot)


def test_unsupported_active_device_set_policy_fails_with_clear_reason() -> None:
    """测试目标：验证当前只支持 single active device set policy。

    测试方法：创建 policy 为 multi 的 ControlService 后注册设备。
    预期结果：注册失败原因明确指出该 policy 未支持。
    """

    service = ControlService(active_device_set_policy="multi")

    response = service.register_device(_registration("user-001", "dev-policy"), FakeConnection("dev-policy"))

    assert response.event_name == "control.device.register.failed"
    assert "unsupported active_device_set_policy" in response.payload["reason"]


def test_debug_api_returns_device_and_user_snapshots() -> None:
    """测试目标：验证 Debug API 可以解释设备、用户和注册失败状态。

    测试方法：通过 AudioChatHttpServer 的 handler 读取 mocked request。
    预期结果：`/api/debug/devices/{device_id}` 和 `/api/debug/users/{user_id}` 返回诊断字段。
    """

    audio_app = AudioChatApp()
    server = AudioChatHttpServer(audio_app)
    audio_app.register_device(
        Event(
            event_name="control.device.register.requested",
            user_id="user-001",
            producer_id="dev-debug",
            payload={
                "device_id": "dev-debug",
                "device_name": "debug",
                "client_type": "python-playback",
                "sdk_version": "audio-chat-test",
                "auth": {"mode": "disabled"},
                "capabilities": {"streams.produce": ["sensor.mic"]},
                "subscriptions": [{"event": "control.audio_session.*"}],
            },
        ),
        FakeConnection("dev-debug"),
    )

    device_response = asyncio.run(
        server.debug_device(make_mocked_request("GET", "/api/debug/devices/dev-debug", match_info={"device_id": "dev-debug"}))
    )
    user_response = asyncio.run(
        server.debug_user(make_mocked_request("GET", "/api/debug/users/user-001", match_info={"user_id": "user-001"}))
    )

    device_payload = json.loads(device_response.text)
    user_payload = json.loads(user_response.text)
    assert device_payload["auth"]["status"] == "passed"
    assert device_payload["binding"]["bound_user_id"] == "user-001"
    assert user_payload["devices"][0]["device_id"] == "dev-debug"
