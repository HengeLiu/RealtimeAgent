"""服务端配置加载与校验模块。"""

from __future__ import annotations

import os
from dataclasses import dataclass

from infra.errors import ErrorCode, build_error


@dataclass(slots=True)
class ServerSettings:
    """服务端配置对象。

    主要功能：
    1. 集中保存服务端运行参数。
    2. 提供环境变量读取与配置合法性校验。

    主要属性：
    1. `host`：监听地址。
    2. `port`：监听端口。
    3. `environment`：运行环境名称，例如 `dev`。
    4. `log_level`：日志级别。
    5. `device_token_map`：设备与配对令牌映射，格式为 `device_id=token,device2=token2`。
    6. `heartbeat_interval_ms`：服务端下发给设备的心跳建议间隔。
    7. `heartbeat_timeout_ms`：服务端判定设备离线的心跳超时时间。
    8. `server_device_id`：服务端在控制消息中的设备编号。
    """

    host: str = "0.0.0.0"
    port: int = 8765
    environment: str = "dev"
    log_level: str = "INFO"
    device_token_map: str = ""
    heartbeat_interval_ms: int = 5000
    heartbeat_timeout_ms: int = 15000
    server_device_id: str = "server-main"

    @classmethod
    def from_env(cls) -> "ServerSettings":
        """从环境变量读取配置。

        主要逻辑：
        1. 读取环境变量，不存在时回落到默认值。
        2. 将 `SERVER_PORT` 转换为整数并进行合法性校验。

        返回值：
        1. `ServerSettings` 实例。

        异常情况：
        1. 端口无法转为整数时抛出 `AppError(INVALID_CONFIG)`。
        2. 校验失败时抛出 `AppError(INVALID_CONFIG)`。
        """

        defaults = cls()

        port_raw = os.getenv("SERVER_PORT", str(defaults.port))
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_PORT 必须是整数",
                details={"value": port_raw},
            ) from exc

        settings = cls(
            host=os.getenv("SERVER_HOST", defaults.host),
            port=port,
            environment=os.getenv("APP_ENV", defaults.environment),
            log_level=os.getenv("LOG_LEVEL", defaults.log_level).upper(),
            device_token_map=os.getenv("DEVICE_TOKEN_MAP", defaults.device_token_map),
            heartbeat_interval_ms=cls._parse_int_env(
                "HEARTBEAT_INTERVAL_MS",
                defaults.heartbeat_interval_ms,
            ),
            heartbeat_timeout_ms=cls._parse_int_env(
                "HEARTBEAT_TIMEOUT_MS",
                defaults.heartbeat_timeout_ms,
            ),
            server_device_id=os.getenv("SERVER_DEVICE_ID", defaults.server_device_id),
        )
        settings.validate()
        return settings

    @staticmethod
    def _parse_int_env(name: str, default: int) -> int:
        """读取整数环境变量。

        参数：
        1. `name`：环境变量名。
        2. `default`：默认值。

        返回值：
        1. 解析后的整数。

        异常情况：
        1. 无法转为整数时抛出 `AppError(INVALID_CONFIG)`。
        """

        raw = os.getenv(name, str(default))
        try:
            return int(raw)
        except ValueError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                f"{name} 必须是整数",
                details={"value": raw},
            ) from exc

    def validate(self) -> None:
        """校验配置合法性。

        主要逻辑：
        1. 校验监听地址非空。
        2. 校验端口在 1~65535。
        3. 校验日志级别在白名单内。

        异常情况：
        1. 任一校验失败时抛出 `AppError(INVALID_CONFIG)`。
        """

        if not self.host.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_HOST 不能为空",
            )
        if not (1 <= self.port <= 65535):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_PORT 必须在 1 到 65535 之间",
                details={"port": self.port},
            )
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if self.log_level not in valid_levels:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "LOG_LEVEL 非法",
                details={"log_level": self.log_level, "valid_levels": sorted(valid_levels)},
            )
        if self.heartbeat_interval_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "HEARTBEAT_INTERVAL_MS 必须大于 0",
                details={"heartbeat_interval_ms": self.heartbeat_interval_ms},
            )
        if self.heartbeat_timeout_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "HEARTBEAT_TIMEOUT_MS 必须大于 0",
                details={"heartbeat_timeout_ms": self.heartbeat_timeout_ms},
            )
        if self.heartbeat_timeout_ms <= self.heartbeat_interval_ms:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "HEARTBEAT_TIMEOUT_MS 必须大于 HEARTBEAT_INTERVAL_MS",
                details={
                    "heartbeat_interval_ms": self.heartbeat_interval_ms,
                    "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
                },
            )
        if not self.server_device_id.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_DEVICE_ID 不能为空",
            )

    def summary(self) -> dict[str, str | int]:
        """生成配置摘要。

        返回值：
        1. 可直接用于日志打印或接口返回的摘要字典。
        """

        return {
            "host": self.host,
            "port": self.port,
            "environment": self.environment,
            "log_level": self.log_level,
            "device_token_count": len(self.parse_device_token_map()),
            "heartbeat_interval_ms": self.heartbeat_interval_ms,
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
            "server_device_id": self.server_device_id,
        }

    def parse_device_token_map(self) -> dict[str, str]:
        """解析设备配对令牌映射。

        主要逻辑：
        1. 输入字符串按逗号切分多个键值对。
        2. 每个键值对按 `=` 拆分为 `device_id` 与 `token`。
        3. 自动跳过空片段。

        返回值：
        1. `device_id -> token` 字典。

        异常情况：
        1. 格式错误时抛出 `AppError(INVALID_CONFIG)`。
        """

        result: dict[str, str] = {}
        raw = self.device_token_map.strip()
        if not raw:
            return result

        for pair in raw.split(","):
            text = pair.strip()
            if not text:
                continue
            if "=" not in text:
                raise build_error(
                    ErrorCode.INVALID_CONFIG,
                    "DEVICE_TOKEN_MAP 格式错误，必须是 device_id=token",
                    details={"item": text},
                )
            device_id, token = text.split("=", 1)
            device_id = device_id.strip()
            token = token.strip()
            if not device_id or not token:
                raise build_error(
                    ErrorCode.INVALID_CONFIG,
                    "DEVICE_TOKEN_MAP 不能包含空的设备编号或令牌",
                    details={"item": text},
                )
            result[device_id] = token
        return result
