"""服务端装配模块。"""

from __future__ import annotations

from api import build_server_handle
from infra.config import ServerSettings


def create_server_handle(settings: ServerSettings):
    """创建默认服务端句柄。

    主要逻辑：
    1. 根据配置构建默认 HTTP 服务句柄。

    返回值：
    1. 默认服务端句柄。
    """

    return build_server_handle(settings)
