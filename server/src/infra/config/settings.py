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
    runtime_tick_seconds: float = 1.0
    runtime_enable_loop: bool = True
    runtime_max_ticks: int = 0
    bailian_endpoint: str = ""
    bailian_api_key: str = ""
    bailian_timeout_seconds: float = 20.0

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
            runtime_tick_seconds=float(os.getenv("OAG_RUNTIME_TICK_SECONDS", str(defaults.runtime_tick_seconds))),
            runtime_enable_loop=os.getenv("OAG_RUNTIME_ENABLE_LOOP", "true").lower() in {"1", "true", "yes", "on"},
            runtime_max_ticks=int(os.getenv("OAG_RUNTIME_MAX_TICKS", str(defaults.runtime_max_ticks))),
            bailian_endpoint=os.getenv("OAG_BAILIAN_ENDPOINT", defaults.bailian_endpoint),
            bailian_api_key=os.getenv("OAG_BAILIAN_API_KEY", defaults.bailian_api_key),
            bailian_timeout_seconds=float(
                os.getenv("OAG_BAILIAN_TIMEOUT_SECONDS", str(defaults.bailian_timeout_seconds))
            ),
        )
