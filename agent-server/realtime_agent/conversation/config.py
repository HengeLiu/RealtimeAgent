from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ConversationRuntimeName = Literal["legacy", "conversation"]


@dataclass(frozen=True, slots=True)
class ConversationRuntimeConfig:
    """conversation runtime 配置。

    主要功能：控制 server 使用旧音视频链路还是新 conversation runtime。
    主要属性：`runtime` 默认是 `legacy`，保证 Phase 0 不改变当前运行行为。
    """

    runtime: ConversationRuntimeName = "legacy"
