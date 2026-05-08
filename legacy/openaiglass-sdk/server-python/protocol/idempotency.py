"""最小幂等索引接口与内存实现。"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod


class IdempotencyStore(ABC):
    """幂等索引接口。

    主要功能：
    1. 定义消息是否已处理的统一查询能力。
    2. 支持后续替换为 Redis 或数据库实现。

    主要方法：
    1. `exists`：判断消息编号是否已存在。
    2. `mark_processed`：记录消息已处理。
    """

    @abstractmethod
    def exists(self, message_id: str) -> bool:
        """判断消息是否已处理。"""

    @abstractmethod
    def mark_processed(self, message_id: str) -> None:
        """标记消息为已处理。"""


class InMemoryIdempotencyStore(IdempotencyStore):
    """内存幂等索引实现。

    主要功能：
    1. 在进程内记录已处理消息。
    2. 基于保留时长自动清理过期记录。

    主要属性：
    1. `ttl_seconds`：消息保留时长。
    2. `_items`：内部存储，值为记录时间戳。
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        """初始化内存幂等索引。

        参数：
        1. `ttl_seconds`：保留时长，单位秒。
        """

        self.ttl_seconds = ttl_seconds
        self._items: dict[str, float] = {}

    def _cleanup(self) -> None:
        """清理过期消息记录。"""

        now = time.time()
        expired = [
            key
            for key, ts in self._items.items()
            if (now - ts) > self.ttl_seconds
        ]
        for key in expired:
            self._items.pop(key, None)

    def exists(self, message_id: str) -> bool:
        """判断消息是否已处理。

        参数：
        1. `message_id`：消息编号。

        返回值：
        1. `True` 表示已处理，`False` 表示未处理。
        """

        self._cleanup()
        return message_id in self._items

    def mark_processed(self, message_id: str) -> None:
        """记录消息处理完成。

        参数：
        1. `message_id`：消息编号。
        """

        self._cleanup()
        self._items[message_id] = time.time()
