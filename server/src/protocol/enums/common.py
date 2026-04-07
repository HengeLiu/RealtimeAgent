from __future__ import annotations

from enum import Enum

try:
    from enum import StrEnum
except ImportError:  # pragma: no cover - py3.10 and lower fallback
    class StrEnum(str, Enum):
        pass


class DeviceType(StrEnum):
    SERVER = "server"
    GLASS = "glass"
    PHONE = "phone"


class DeviceStatus(StrEnum):
    OFFLINE = "offline"
    REGISTERING = "registering"
    ONLINE = "online"
    BUSY = "busy"
    SLEEPING = "sleeping"
    DEGRADED = "degraded"
    RECONNECTING = "reconnecting"
    ERROR = "error"


class BindingStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    BROKEN = "broken"
    REVOKED = "revoked"


class SessionType(StrEnum):
    PAIRING = "pairing_session"
    CONVERSATION = "conversation_session"
    TASK = "task_session"


class SessionStatus(StrEnum):
    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    ENDED = "ended"
    FAILED = "failed"


class TaskSource(StrEnum):
    USER_DIRECT = "user_direct"
    AGENT = "agent"
    SKILL = "skill"
    SCHEDULER = "scheduler"
    SYSTEM = "system"


class TaskStatus(StrEnum):
    CREATED = "created"
    QUEUED = "queued"
    PREPARING = "preparing"
    RUNNING = "running"
    WAITING_INPUT = "waiting_input"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class SkillMode(StrEnum):
    SYNC = "sync"
    ASYNC = "async"
    TASK_SPAWN = "task_spawn"


class SkillCallStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class MediaType(StrEnum):
    AUDIO_CHUNK = "audio_chunk"
    AUDIO_FILE = "audio_file"
    IMAGE = "image"
    VIDEO_FRAME = "video_frame"
    IMU_CHUNK = "imu_chunk"


class ErrorType(StrEnum):
    VALIDATION = "validation_error"
    AUTH = "auth_error"
    NETWORK = "network_error"
    DEVICE = "device_error"
    UPSTREAM = "upstream_error"
    TIMEOUT = "timeout_error"
    CONFLICT = "conflict_error"
    INTERNAL = "internal_error"


class AckStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    PROCESSED = "processed"
