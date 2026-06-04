from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ConversationRuntimeName = Literal["conversation"]


@dataclass(frozen=True, slots=True)
class ConversationRuntimeConfig:
    """conversation runtime 配置。

    主要功能：表达当前 server 使用正式 conversation runtime。该字段保留为配置兼容
    入口，旧 legacy runtime 不再参与正式 app 装配。
    主要属性：`runtime` 默认是 `conversation`。
    """

    runtime: ConversationRuntimeName = "conversation"
