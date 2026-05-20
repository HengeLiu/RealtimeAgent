from __future__ import annotations

from realtime_agent.app import RealtimeAgentApp, RealtimeAgentConfig


def create_app(config: RealtimeAgentConfig | None = None) -> RealtimeAgentApp:
    """创建测试用 basic app。"""

    return RealtimeAgentApp(config or RealtimeAgentConfig())
