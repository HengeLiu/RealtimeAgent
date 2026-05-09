from __future__ import annotations

from audio_chat.control import ControlService, DeviceAuthenticator, HmacSignedTokenIssuer
from audio_chat.protocol import Event


class FakeConnection:
    """测试用控制连接。

    主要功能：记录下发事件和关闭原因，避免测试依赖真实 WebSocket。
    主要方法：`push_event()` 记录事件，`close()` 记录关闭原因。
    主要属性：`device_id` 为设备编号，`closed_reason` 为最近一次关闭原因。
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


def _registration(user_id: str, device_id: str, token: str) -> Event:
    """构造 signed_token 注册事件。

    主要逻辑：注册事件只携带语义字段和 token，不放任何媒体大字节。
    参数：用户编号、设备编号和 token。
    返回值：`control.device.register.requested` 事件。
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
            "auth": {"mode": "signed_token", "token": token},
            "routes": [{"event": "control.audio_session.*"}],
        },
    )


def _service(monkeypatch, *, now: float = 1_000.0) -> ControlService:
    """创建 signed_token 鉴权服务。

    测试目标：统一注入环境变量密钥和固定时间，避免测试受本机环境影响。
    测试方法：通过 monkeypatch 设置 `AUDIO_CHAT_DEVICE_TOKEN_SECRET`。
    预期结果：所有 token 校验都使用同一密钥和时间源。
    """

    monkeypatch.setenv("AUDIO_CHAT_DEVICE_TOKEN_SECRET", "test-secret")
    return ControlService(
        authenticator=DeviceAuthenticator(
            mode="signed_token",
            signed_token_secret_env="AUDIO_CHAT_DEVICE_TOKEN_SECRET",
            token_clock_skew_seconds=60,
            now=lambda: now,
        )
    )


def test_signed_token_accepts_valid_token(monkeypatch) -> None:
    """测试目标：验证 signed_token 正常注册不再返回未实现。

    测试方法：用 HMAC issuer 签发包含 user_id、device_id、expires_at、nonce 的 token。
    预期结果：注册成功，debug snapshot 标记 auth 通过且不包含原始 token。
    """

    service = _service(monkeypatch)
    token = HmacSignedTokenIssuer("test-secret").issue_token(
        user_id="user-001",
        device_id="dev-001",
        expires_at=1_100,
        nonce="nonce-001",
    )

    response = service.register_device(_registration("user-001", "dev-001", token), FakeConnection("dev-001"))
    snapshot = service.build_device_snapshot("dev-001")

    assert response.event_name == "control.device.registered"
    assert response.payload["device_id"] == "dev-001"
    assert response.payload["connection_id"]
    assert snapshot["auth"]["mode"] == "signed_token"
    assert snapshot["auth"]["status"] == "passed"
    assert token not in str(snapshot)


def test_signed_token_rejects_expired_token(monkeypatch) -> None:
    """测试目标：验证过期 token 有明确失败原因。

    测试方法：签发 expires_at 早于当前时间和 skew 的 token。
    预期结果：注册失败 reason 为 `signed_token_expired`。
    """

    service = _service(monkeypatch, now=1_000)
    token = HmacSignedTokenIssuer("test-secret").issue_token(
        user_id="user-001",
        device_id="dev-expired",
        expires_at=900,
        nonce="nonce-expired",
    )

    response = service.register_device(_registration("user-001", "dev-expired", token), FakeConnection("dev-expired"))

    assert response.event_name == "control.device.register.failed"
    assert response.payload["reason"] == "signed_token_expired"


def test_signed_token_rejects_user_mismatch(monkeypatch) -> None:
    """测试目标：验证 token 中 user_id 与注册事件不一致时拒绝注册。

    测试方法：token 签给 `user-001`，注册事件使用 `user-002`。
    预期结果：失败原因是 `signed_token_user_mismatch`。
    """

    service = _service(monkeypatch)
    token = HmacSignedTokenIssuer("test-secret").issue_token(
        user_id="user-001",
        device_id="dev-user",
        expires_at=1_100,
        nonce="nonce-user",
    )

    response = service.register_device(_registration("user-002", "dev-user", token), FakeConnection("dev-user"))

    assert response.event_name == "control.device.register.failed"
    assert response.payload["reason"] == "signed_token_user_mismatch"


def test_signed_token_rejects_device_mismatch(monkeypatch) -> None:
    """测试目标：验证 token 中 device_id 与注册事件不一致时拒绝注册。

    测试方法：token 签给 `dev-a`，注册事件使用 `dev-b`。
    预期结果：失败原因是 `signed_token_device_mismatch`。
    """

    service = _service(monkeypatch)
    token = HmacSignedTokenIssuer("test-secret").issue_token(
        user_id="user-001",
        device_id="dev-a",
        expires_at=1_100,
        nonce="nonce-device",
    )

    response = service.register_device(_registration("user-001", "dev-b", token), FakeConnection("dev-b"))

    assert response.event_name == "control.device.register.failed"
    assert response.payload["reason"] == "signed_token_device_mismatch"


def test_signed_token_rejects_bad_signature(monkeypatch) -> None:
    """测试目标：验证错误签名有明确失败原因。

    测试方法：用错误密钥签发 token，再用服务端密钥校验。
    预期结果：失败原因是 `invalid_signed_token_signature`。
    """

    service = _service(monkeypatch)
    token = HmacSignedTokenIssuer("wrong-secret").issue_token(
        user_id="user-001",
        device_id="dev-signature",
        expires_at=1_100,
        nonce="nonce-signature",
    )

    response = service.register_device(_registration("user-001", "dev-signature", token), FakeConnection("dev-signature"))

    assert response.event_name == "control.device.register.failed"
    assert response.payload["reason"] == "invalid_signed_token_signature"


def test_pairing_token_issuer_can_be_faked_for_management_service(monkeypatch) -> None:
    """测试目标：确认 PairingTokenIssuer 边界可由配对服务替换。

    测试方法：定义一个 fake issuer，返回测试 token，再走真实 ControlService 校验。
    预期结果：fake issuer 签发的 token 可用于注册，证明接口边界可替换。
    """

    class FakeIssuer(HmacSignedTokenIssuer):
        def issue_for_test(self) -> str:
            return self.issue_token(user_id="user-001", device_id="dev-fake", expires_at=1_100, nonce="fake-nonce")

    service = _service(monkeypatch)
    token = FakeIssuer("test-secret").issue_for_test()

    response = service.register_device(_registration("user-001", "dev-fake", token), FakeConnection("dev-fake"))

    assert response.event_name == "control.device.registered"
