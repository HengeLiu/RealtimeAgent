"""
设备配对服务 — 管理配对码生成、验证和设备绑定
"""
import logging
import random
import string
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

CODE_TTL_SECONDS = 600  # 10 分钟
TOKEN_TTL_SECONDS = 30 * 24 * 3600  # 30 天


@dataclass
class PairingCode:
    code: str
    user_id: str
    created_at: float
    expires_at: float
    used: bool = False


@dataclass
class PairingResult:
    user_id: str
    device_id: str
    auth_token: str
    server_host: str
    server_port: int


@dataclass
class RegisteredDevice:
    """自注册设备信息（无配对码）"""
    hardware_id: str
    device_id: str
    auth_token: str
    bound: bool = False
    user_id: str = ""
    registered_at: float = field(default_factory=time.time)


class PairingService:
    """配对码管理 + 设备绑定"""

    def __init__(self, token_issuer, server_host: str = "192.168.31.8", server_port: int = 8766):
        self._token_issuer = token_issuer
        self._server_host = server_host
        self._server_port = server_port
        self._codes: dict[str, PairingCode] = {}  # code -> PairingCode
        self._bindings: dict[str, str] = {}  # hardware_id -> device_id
        self._registered: dict[str, RegisteredDevice] = {}  # hardware_id -> RegisteredDevice

    def generate_pairing_code(self, user_id: str) -> str:
        """为用户生成 6 位配对码"""
        # 清理过期码
        self._cleanup_expired()

        # 生成唯一码
        code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
        while code in self._codes:
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))

        now = time.time()
        self._codes[code] = PairingCode(
            code=code,
            user_id=user_id,
            created_at=now,
            expires_at=now + CODE_TTL_SECONDS,
        )
        logger.info(f"Generated pairing code {code} for user {user_id}")
        return code

    def validate_and_pair(self, pairing_code: str, hardware_id: str, device_name: str = "") -> PairingResult:
        """验证配对码，绑定设备，返回注册信息"""
        self._cleanup_expired()

        pc = self._codes.get(pairing_code)
        if not pc:
            raise ValueError("invalid_pairing_code")

        if pc.used:
            raise ValueError("pairing_code_already_used")

        if time.time() > pc.expires_at:
            raise ValueError("pairing_code_expired")

        # 生成 device_id（基于 hardware_id）
        if hardware_id in self._bindings:
            device_id = self._bindings[hardware_id]
        else:
            # hw-a1b2c3d4e5f6 -> dev-glass-c3d4e5f6
            short = hardware_id.replace("hw-", "")[-8:] if hardware_id.startswith("hw-") else hardware_id[-8:]
            device_id = f"dev-glass-{short}"
            self._bindings[hardware_id] = device_id

        # 签发 signed_token
        nonce = uuid.uuid4().hex
        expires_at = int(time.time()) + TOKEN_TTL_SECONDS
        auth_token = self._token_issuer.issue_token(
            user_id=pc.user_id,
            device_id=device_id,
            expires_at=expires_at,
            nonce=nonce,
        )

        # 标记配对码已使用
        pc.used = True

        logger.info(f"Device paired: {hardware_id} -> {device_id} for user {pc.user_id}")
        return PairingResult(
            user_id=pc.user_id,
            device_id=device_id,
            auth_token=auth_token,
            server_host=self._server_host,
            server_port=self._server_port,
        )

    def register_device(self, hardware_id: str, device_name: str = "") -> RegisteredDevice:
        """设备自注册（无需配对码）。ESP32 调用。"""
        if hardware_id in self._registered:
            existing = self._registered[hardware_id]
            logger.info(f"Device already registered: {hardware_id} -> {existing.device_id}")
            return existing

        # 生成 device_id
        short = hardware_id.replace("hw-", "")[-8:] if hardware_id.startswith("hw-") else hardware_id[-8:]
        device_id = f"dev-glass-{short}"
        self._bindings[hardware_id] = device_id

        # 签发 token（user_id 为占位符，绑定后会更新）
        nonce = uuid.uuid4().hex
        expires_at = int(time.time()) + TOKEN_TTL_SECONDS
        auth_token = self._token_issuer.issue_token(
            user_id="unbound",
            device_id=device_id,
            expires_at=expires_at,
            nonce=nonce,
        )

        reg = RegisteredDevice(
            hardware_id=hardware_id,
            device_id=device_id,
            auth_token=auth_token,
            bound=False,
        )
        self._registered[hardware_id] = reg
        logger.info(f"Device self-registered: {hardware_id} -> {device_id}")
        return reg

    def bind_device(self, hardware_id: str, user_id: str) -> RegisteredDevice:
        """用户绑定设备。App 调用。"""
        reg = self._registered.get(hardware_id)
        if not reg:
            raise ValueError("device_not_registered")
        if reg.bound:
            raise ValueError("device_already_bound")

        # 重新签发带真实 user_id 的 token
        nonce = uuid.uuid4().hex
        expires_at = int(time.time()) + TOKEN_TTL_SECONDS
        auth_token = self._token_issuer.issue_token(
            user_id=user_id,
            device_id=reg.device_id,
            expires_at=expires_at,
            nonce=nonce,
        )

        reg.user_id = user_id
        reg.auth_token = auth_token
        reg.bound = True
        logger.info(f"Device bound: {hardware_id} -> {user_id}")
        return reg

    def get_registered_devices(self) -> list[RegisteredDevice]:
        """返回所有已注册设备（供 debug API 使用）"""
        return list(self._registered.values())

    def _cleanup_expired(self):
        now = time.time()
        expired = [k for k, v in self._codes.items() if now > v.expires_at]
        for k in expired:
            del self._codes[k]
