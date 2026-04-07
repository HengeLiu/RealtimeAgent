from __future__ import annotations

from uuid import uuid4


class IdGenerator:
    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid4().hex[:12]}"

    @classmethod
    def message_id(cls) -> str:
        return cls.new_id("msg")

    @classmethod
    def trace_id(cls) -> str:
        return cls.new_id("trace")

    @classmethod
    def task_id(cls) -> str:
        return cls.new_id("task")

    @classmethod
    def session_id(cls) -> str:
        return cls.new_id("sess")

    @classmethod
    def skill_call_id(cls) -> str:
        return cls.new_id("skill")
