from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = "openai_glasses_server"
    protocol_version: str = "1.0.0"
    log_level: str = "INFO"
    heartbeat_interval_seconds: int = 10
    heartbeat_timeout_seconds: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            app_name=os.getenv("OAG_APP_NAME", defaults.app_name),
            protocol_version=os.getenv("OAG_PROTOCOL_VERSION", defaults.protocol_version),
            log_level=os.getenv("OAG_LOG_LEVEL", defaults.log_level),
            heartbeat_interval_seconds=int(
                os.getenv("OAG_HEARTBEAT_INTERVAL_SECONDS", str(defaults.heartbeat_interval_seconds))
            ),
            heartbeat_timeout_seconds=int(
                os.getenv("OAG_HEARTBEAT_TIMEOUT_SECONDS", str(defaults.heartbeat_timeout_seconds))
            ),
        )
