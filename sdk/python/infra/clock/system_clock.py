"""系统时钟实现。"""

from __future__ import annotations

import time


class SystemClock:
    """提供当前系统时间。

    主要功能：
    1. 统一提供毫秒时间戳。
    2. 便于后续测试中替换为可控时钟。
    """

    def now_ms(self) -> int:
        """返回当前 Unix 毫秒时间戳。"""

        return int(time.time() * 1000)

