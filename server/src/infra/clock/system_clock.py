from __future__ import annotations

from datetime import datetime, timezone


class SystemClock:
    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def now_iso() -> str:
        return SystemClock.now().isoformat()
